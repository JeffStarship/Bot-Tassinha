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
