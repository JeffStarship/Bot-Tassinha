"""
Ferramenta de indicação: registra quem indicou quem.
"""
from database import get_client


def registrar_indicacao(indicadora_id: str, indicada_id: str, data: str) -> dict:
    """data no formato YYYY-MM-DD"""
    db = get_client()
    payload = {
        "indicadora_id": indicadora_id,
        "indicada_id": indicada_id,
        "data": data,
    }
    resp = db.table("indicacoes").insert(payload).execute()

    # Marca o canal de aquisição da indicada, se ainda não tiver
    db.table("clientes").update({
        "canal_aquisicao": "indicacao",
        "indicada_por_id": indicadora_id,
    }).eq("id", indicada_id).execute()

    return {"registrado": True, "indicacao": resp.data[0]}


def consultar_indicacoes(indicadora_id: str) -> dict:
    """
    Retorna os contadores de indicação de uma cliente:
    - total_indicado: quantas pessoas ela indicou (convertidas ou não)
    - total_convertido: quantas dessas viraram cliente (fizeram a 1ª visita)
    - ativas: convertidas dentro da validade da campanha (este é o número que
      vale pro abatimento agora)
    Use o cliente_id de buscar_cliente.
    """
    db = get_client()
    resp = db.rpc("indicacoes_cliente", {"p_indicadora_id": indicadora_id}).execute()
    if not resp.data:
        return {"total_indicado": 0, "total_convertido": 0, "ativas": 0, "validade_meses": 2}
    r = resp.data[0]
    return {
        "total_indicado": int(r["total_indicado"]),
        "total_convertido": int(r["total_convertido"]),
        "ativas": int(r["ativas"]),
        "validade_meses": int(r["validade_meses"]),
    }


def listar_indicacoes_ativas(indicadora_id: str) -> dict:
    """
    Lista as indicações que estão valendo agora pra uma cliente (convertidas
    dentro da validade), com nome de cada indicada e quantos dias restam.
    """
    db = get_client()
    resp = db.rpc("indicacoes_ativas_cliente", {"p_indicadora_id": indicadora_id}).execute()
    ativas = [
        {"nome": r["nome"], "primeira_visita": r["primeira_visita"],
         "dias_restantes": int(r["dias_restantes"])}
        for r in (resp.data or [])
    ]
    if not ativas:
        return {"total": 0, "ativas": [], "obs": "nenhuma indicação ativa no momento"}
    return {"total": len(ativas), "ativas": ativas}


def ranking_indicadoras(limite: int = 10) -> dict:
    """
    Ranking das clientes que mais indicaram (ordenado por convertidas).
    Use pra 'quem mais me indicou clientes'.
    """
    db = get_client()
    resp = db.rpc("ranking_indicadoras", {"p_limite": limite}).execute()
    ranking = [
        {"nome": r["nome"], "total_indicado": int(r["total_indicado"]),
         "total_convertido": int(r["total_convertido"]), "ativas": int(r["ativas"])}
        for r in (resp.data or [])
    ]
    if not ranking:
        return {"total": 0, "ranking": [], "obs": "nenhuma indicação registrada ainda"}
    return {"total": len(ranking), "ranking": ranking}
