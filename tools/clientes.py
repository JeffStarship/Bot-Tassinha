"""
Ferramentas relacionadas a clientes: buscar, criar, atualizar.
Cada função aqui é exposta ao agente como uma "tool" que o Gemini
pode chamar. Toda escrita/leitura passa por aqui — a IA nunca toca
no banco diretamente.
"""
from database import get_client


def normalizar_telefone(tel: str) -> str | None:
    """
    Normaliza telefone brasileiro pro formato wa.me: 55 + DDD + número, só dígitos.
    Feito UMA vez no cadastro/atualização — o botão de WhatsApp só lê o que já
    está limpo no banco.
    Aceita formatos variados ((48) 99999-9999, 48999999999, +55 48 9..., etc).
    Retorna None se não der pra extrair um número plausível.
    """
    if not tel:
        return None
    digitos = "".join(c for c in str(tel) if c.isdigit())
    if not digitos:
        return None
    # remove zeros à esquerda (ex: 0 antes do DDD)
    digitos = digitos.lstrip("0")
    # já tem 55 na frente e tamanho de número completo (12-13 dígitos)
    if digitos.startswith("55") and len(digitos) in (12, 13):
        return digitos
    # 10 (fixo DDD+8) ou 11 (cel DDD+9) dígitos -> adiciona 55
    if len(digitos) in (10, 11):
        return "55" + digitos
    # 8 ou 9 dígitos (sem DDD) -> não dá pra montar wa.me confiável
    if len(digitos) in (8, 9):
        return None
    # qualquer outra coisa: devolve como está se já parecer ter 55, senão None
    if digitos.startswith("55") and len(digitos) >= 12:
        return digitos
    return None


def buscar_cliente(nome: str) -> dict:
    """
    Busca cliente por nome (case-insensitive, busca parcial).
    Retorna lista de matches — se mais de um, o agente deve perguntar
    qual é a correta antes de agir.
    """
    db = get_client()
    resp = (
        db.table("clientes")
        .select("*")
        .ilike("nome", f"%{nome}%")
        .execute()
    )
    return {
        "encontrados": len(resp.data),
        "clientes": resp.data,
    }


def criar_cliente(
    nome: str,
    telefone: str = None,
    instagram: str = None,
    canal_aquisicao: str = "outro",
    indicada_por_id: str = None,
    observacoes: str = None,
) -> dict:
    """
    Cria uma cliente nova. canal_aquisicao deve ser um de:
    marketplace, indicacao, instagram, boost, outro
    """
    db = get_client()
    payload = {
        "nome": nome,
        "telefone": normalizar_telefone(telefone) if telefone else None,
        "instagram": instagram,
        "canal_aquisicao": canal_aquisicao,
        "indicada_por_id": indicada_por_id,
        "observacoes": observacoes,
        "status": "lead",
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    resp = db.table("clientes").insert(payload).execute()
    return {"criado": True, "cliente": resp.data[0]}


def atualizar_cliente(cliente_id: str, campos: dict) -> dict:
    """
    Atualiza campos de uma cliente existente.
    campos é um dict com as chaves a atualizar
    (ex: {"telefone": "...", "observacoes": "..."})
    """
    db = get_client()
    # normaliza telefone se estiver sendo atualizado
    if "telefone" in campos and campos["telefone"]:
        campos = dict(campos)
        campos["telefone"] = normalizar_telefone(campos["telefone"])
    resp = (
        db.table("clientes")
        .update(campos)
        .eq("id", cliente_id)
        .execute()
    )
    if not resp.data:
        return {"atualizado": False, "erro": "cliente não encontrada"}
    return {"atualizado": True, "cliente": resp.data[0]}
