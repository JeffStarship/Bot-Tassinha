"""
Ferramentas de pagamento: registrar sinal e fechamento de atendimento.
"""
from database import get_client


def registrar_pagamento(
    atendimento_id: str,
    tipo: str,
    valor: float,
    forma: str,
    data: str,
) -> dict:
    """
    tipo: sinal, restante, total
    forma: pix, dinheiro, cartao
    data no formato YYYY-MM-DD
    """
    db = get_client()
    payload = {
        "atendimento_id": atendimento_id,
        "tipo": tipo,
        "valor": valor,
        "forma": forma,
        "data": data,
    }
    resp = db.table("pagamentos").insert(payload).execute()
    return {"registrado": True, "pagamento": resp.data[0]}


def saldo_atendimento(atendimento_id: str) -> dict:
    """
    Mostra quanto já foi pago e quanto falta pra um atendimento,
    com base no valor total do atendimento menos os pagamentos
    já registrados.
    """
    db = get_client()
    atendimento = (
        db.table("atendimentos")
        .select("valor")
        .eq("id", atendimento_id)
        .execute()
    )
    if not atendimento.data:
        return {"erro": "atendimento não encontrado"}

    valor_total = atendimento.data[0]["valor"]
    pagamentos = (
        db.table("pagamentos")
        .select("valor")
        .eq("atendimento_id", atendimento_id)
        .execute()
    )
    pago = sum(p["valor"] for p in pagamentos.data)
    return {
        "valor_total": valor_total,
        "pago": pago,
        "falta": round(valor_total - pago, 2),
    }
