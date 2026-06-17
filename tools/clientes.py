"""
Ferramentas relacionadas a clientes: buscar, criar, atualizar.
Cada função aqui é exposta ao agente como uma "tool" que o Gemini
pode chamar. Toda escrita/leitura passa por aqui — a IA nunca toca
no banco diretamente.
"""
from database import get_client


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
        "telefone": telefone,
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
    resp = (
        db.table("clientes")
        .update(campos)
        .eq("id", cliente_id)
        .execute()
    )
    if not resp.data:
        return {"atualizado": False, "erro": "cliente não encontrada"}
    return {"atualizado": True, "cliente": resp.data[0]}
