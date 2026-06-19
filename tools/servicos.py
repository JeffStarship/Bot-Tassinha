"""
Ferramentas de serviços (catálogo de preços).

A Tassia cadastra os serviços em texto livre — o Haiku interpreta o texto e
chama cadastrar_servico uma vez por serviço identificado. Reenvio do mesmo
nome atualiza o preço (upsert case-insensitive no banco).

Quando ela registra um atendimento dizendo só o nome do serviço, o agente usa
buscar_preco_servico pra puxar o valor automaticamente.
"""
from database import get_client


def cadastrar_servico(nome: str, preco: float, duracao_min: int = None) -> dict:
    """
    Cadastra ou atualiza UM serviço no catálogo de preços.
    Se o nome já existe (ignorando maiúsculas), atualiza o preço.
    duracao_min é opcional — se omitido, usa a duração padrão (90 min).

    Para cadastrar vários de uma vez, chame esta ferramenta uma vez por serviço.
    """
    db = get_client()
    params = {"p_nome": nome, "p_preco": preco}
    if duracao_min is not None:
        params["p_duracao_min"] = duracao_min
    resp = db.rpc("upsert_servico", params).execute()
    if not resp.data:
        return {"ok": False, "erro": "não consegui salvar o serviço"}
    r = resp.data[0]
    return {
        "ok": True,
        "acao": r["acao"],  # 'criado' ou 'atualizado'
        "servico": {"nome": r["nome"], "preco": float(r["preco"]), "duracao_min": r["duracao_min"]},
    }


def listar_servicos() -> dict:
    """Lista todos os serviços ativos do catálogo, com preço e duração."""
    db = get_client()
    resp = (
        db.table("servicos")
        .select("nome, preco, duracao_min")
        .eq("ativo", True)
        .order("nome")
        .execute()
    )
    itens = [
        {"nome": s["nome"], "preco": float(s["preco"]), "duracao_min": s["duracao_min"]}
        for s in (resp.data or [])
    ]
    if not itens:
        return {"total": 0, "servicos": [], "obs": "nenhum serviço cadastrado ainda"}
    return {"total": len(itens), "servicos": itens}


def buscar_preco_servico(nome: str) -> dict:
    """
    Busca o preço de um serviço pelo nome (parcial, ignora maiúsculas).
    Use antes de registrar um atendimento quando a Tassia disser só o nome
    do serviço, pra puxar o valor automaticamente.

    Retorna:
    - 1 match: o serviço com preço e duração.
    - vários matches: lista pra desambiguar (pergunte qual à Tassia).
    - 0 matches: serviço não cadastrado (ofereça cadastrar perguntando o preço).
    """
    db = get_client()
    resp = db.rpc("buscar_servico", {"p_nome": nome}).execute()
    matches = [
        {"nome": s["nome"], "preco": float(s["preco"]), "duracao_min": s["duracao_min"]}
        for s in (resp.data or [])
    ]
    if not matches:
        return {"encontrados": 0, "servicos": [], "obs": "serviço não cadastrado"}
    if len(matches) == 1:
        return {"encontrados": 1, "servico": matches[0]}
    return {"encontrados": len(matches), "servicos": matches}
