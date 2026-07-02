"""
Ferramentas de sinal / entrada de pagamento (Sessão 15).

Sinal = pagamento parcial antecipado que confirma o agendamento. Vive em
`pagamentos` (tipo='sinal'), ligado ao atendimento. Pode ter vários (somam).

Estados de pagamento (derivados): sem_sinal | parcial | integral.
Crédito = sinal preservado de um cancelamento, guardado na cliente até ser
aplicado (com confirmação) num atendimento futuro.

Regras (decididas com o Paulo):
- Sinal nunca >= total (é confirmação). Se registrarem >=, ALERTA a Tassia.
- Pagamento integral adiantado -> flag pago_integral; lembretes dizem "tudo pago".
- Remarcar preserva o sinal (pagamentos seguem ligados ao mesmo atendimento).
- Cancelar: a Tassia decide — guardar como crédito ou reter (perder = receita).
- Crédito só é aplicado com confirmação da Tassia.
- Sem regra de tempo: a palavra da Tassia decide sempre.
"""
from datetime import date
from database import get_client


def _proximo_agendamento(cliente_id: str):
    """Acha o próximo atendimento agendado futuro da cliente."""
    db = get_client()
    hoje = date.today().isoformat()
    resp = (
        db.table("atendimentos")
        .select("id, data, hora, servico, valor")
        .eq("cliente_id", cliente_id)
        .eq("status", "agendado")
        .gte("data", hoje)
        .order("data")
        .execute()
    )
    return resp.data[0] if resp.data else None


def registrar_sinal(cliente_id: str, valor: float, data_pagamento: str = None,
                    forma: str = None, atendimento_id: str = None) -> dict:
    """
    Registra um sinal (entrada) pro próximo agendamento da cliente (ou um
    atendimento específico via atendimento_id). Pode haver vários sinais no
    mesmo atendimento — somam.

    ALERTA: se o sinal (somado aos já pagos) ficar >= o valor total do
    atendimento, NÃO grava e devolve um alerta pra Tassia confirmar/corrigir,
    porque sinal é parcial. Se ela quis dizer que pagou tudo, use
    marcar_pago_integral.

    Requer que o atendimento tenha valor total definido (>0) pra calcular o
    restante. Se não tiver, devolve pedido de definir o serviço/valor.
    """
    db = get_client()
    if atendimento_id:
        resp = db.table("atendimentos").select("id, data, valor, servico").eq("id", atendimento_id).execute()
        ag = resp.data[0] if resp.data else None
    else:
        ag = _proximo_agendamento(cliente_id)
    if not ag:
        return {"ok": False, "erro": "nenhum agendamento futuro pra essa cliente"}

    total = float(ag.get("valor") or 0)
    if total <= 0:
        return {"ok": False, "precisa_valor": True,
                "obs": "o agendamento não tem valor definido — informe o serviço/valor primeiro pra eu calcular o restante"}

    # soma sinais já pagos
    ja = db.rpc("sinal_do_atendimento", {"p_atendimento_id": ag["id"]}).execute()
    ja_pago = float(ja.data or 0)
    novo_total_sinal = ja_pago + valor

    if novo_total_sinal >= total:
        return {"ok": False, "alerta_sinal_alto": True,
                "total_servico": total, "sinais_ja_pagos": ja_pago, "sinal_tentado": valor,
                "obs": f"esse sinal deixaria o pago (R${novo_total_sinal:.2f}) maior ou igual ao "
                       f"valor do serviço (R${total:.2f}). Sinal é parcial. Confere o valor, ou "
                       f"se a cliente pagou tudo adiantado me diz que marco como pago integral."}

    payload = {"atendimento_id": ag["id"], "tipo": "sinal", "valor": valor,
               "data": data_pagamento or date.today().isoformat()}
    if forma:
        payload["forma"] = forma
    db.table("pagamentos").insert(payload).execute()

    falta = total - novo_total_sinal
    return {"ok": True, "registrado": True, "atendimento_id": ag["id"],
            "servico": ag.get("servico"), "data_atendimento": ag["data"],
            "sinal_pago_agora": valor, "sinal_total_pago": novo_total_sinal,
            "total_servico": total, "falta_receber": falta}


def marcar_pago_integral(cliente_id: str, valor: float = None,
                         data_pagamento: str = None, forma: str = None,
                         atendimento_id: str = None) -> dict:
    """
    Marca o próximo agendamento (ou um específico) como PAGO INTEGRALMENTE
    adiantado. Use quando a Tassia disser que a cliente já pagou tudo antes.
    Os lembretes vão dizer "já está tudo pago" em vez de falar de restante.
    """
    db = get_client()
    if atendimento_id:
        resp = db.table("atendimentos").select("id, valor, servico, data").eq("id", atendimento_id).execute()
        ag = resp.data[0] if resp.data else None
    else:
        ag = _proximo_agendamento(cliente_id)
    if not ag:
        return {"ok": False, "erro": "nenhum agendamento futuro pra essa cliente"}
    total = float(ag.get("valor") or 0)
    valor_pago = valor if valor is not None else total
    db.table("pagamentos").insert({
        "atendimento_id": ag["id"], "tipo": "total", "valor": valor_pago,
        "data": data_pagamento or date.today().isoformat(),
        **({"forma": forma} if forma else {}),
    }).execute()
    db.table("atendimentos").update({"pago_integral": True}).eq("id", ag["id"]).execute()
    return {"ok": True, "pago_integral": True, "servico": ag.get("servico"),
            "data_atendimento": ag["data"], "valor_pago": valor_pago}


def ver_pagamento(cliente_id: str, atendimento_id: str = None) -> dict:
    """
    Mostra o estado de pagamento do próximo agendamento (ou específico):
    total, quanto já foi pago de sinal, e quanto falta. Use pra 'quanto a
    Bruna já pagou', 'falta quanto da Bruna'.
    """
    db = get_client()
    if atendimento_id:
        aid = atendimento_id
    else:
        ag = _proximo_agendamento(cliente_id)
        if not ag:
            return {"ok": False, "erro": "nenhum agendamento futuro pra essa cliente"}
        aid = ag["id"]
    r = db.rpc("estado_pagamento", {"p_atendimento_id": aid}).execute()
    if not r.data:
        return {"ok": False, "erro": "sem dados"}
    d = r.data[0]
    return {"ok": True, "total": float(d["total"]), "pago": float(d["pago"]),
            "falta": float(d["falta"]), "estado": d["estado"],
            "pago_integral": d["pago_integral"]}


def registrar_pagamento_restante(cliente_id: str, atendimento_id: str = None,
                                 forma: str = None) -> dict:
    """
    Registra o recebimento do valor RESTANTE de um atendimento (o que faltava
    depois do sinal). Calcula o restante sozinho. Use no fechamento: 'recebi o
    restante da Bruna'. Normalmente é chamado junto ao concluir o atendimento.
    """
    db = get_client()
    if atendimento_id:
        aid = atendimento_id
    else:
        ag = _proximo_agendamento(cliente_id)
        if not ag:
            return {"ok": False, "erro": "nenhum agendamento futuro pra essa cliente"}
        aid = ag["id"]
    r = db.rpc("estado_pagamento", {"p_atendimento_id": aid}).execute()
    d = r.data[0]
    falta = float(d["falta"])
    if falta <= 0:
        return {"ok": True, "nada_a_receber": True, "obs": "esse atendimento já está quitado"}
    db.table("pagamentos").insert({
        "atendimento_id": aid, "tipo": "restante", "valor": falta,
        "data": date.today().isoformat(), **({"forma": forma} if forma else {}),
    }).execute()
    return {"ok": True, "recebido": falta, "quitado": True}


# ---- Crédito (sinal preservado de cancelamento) --------------------------

def guardar_credito(cliente_id: str, valor: float, origem_atendimento_id: str = None) -> dict:
    """
    Guarda um valor como crédito da cliente (sinal preservado num cancelamento).
    Use quando a Tassia, ao cancelar, decidir que o sinal vale pra próxima.
    O crédito fica ativo até ser aplicado (com confirmação) num atendimento.
    """
    db = get_client()
    payload = {"cliente_id": cliente_id, "valor": valor, "status": "ativo"}
    if origem_atendimento_id:
        payload["origem_atendimento_id"] = origem_atendimento_id
    db.table("creditos_cliente").insert(payload).execute()
    return {"ok": True, "credito_guardado": valor}


def registrar_sinal_retido(atendimento_id: str, valor: float) -> dict:
    """
    Registra que um sinal foi RETIDO (perdido pela cliente) — vira receita da
    Tassia no dia de hoje. Use quando a Tassia, ao cancelar/no-show, decidir
    que a cliente perdeu o sinal.
    """
    db = get_client()
    db.table("pagamentos").insert({
        "atendimento_id": atendimento_id, "tipo": "sinal_retido", "valor": valor,
        "data": date.today().isoformat(),
    }).execute()
    return {"ok": True, "retido": valor, "obs": "entrou como receita hoje e na métrica de retidos"}


def ver_creditos(cliente_id: str) -> dict:
    """
    Mostra os créditos ativos de uma cliente (valores guardados de sinais
    preservados). Use pra 'a Bruna tem crédito?'.
    """
    db = get_client()
    r = db.rpc("creditos_ativos_cliente", {"p_cliente_id": cliente_id}).execute()
    creditos = [{"id": c["id"], "valor": float(c["valor"])} for c in (r.data or [])]
    if not creditos:
        return {"ok": True, "tem_credito": False, "total": 0}
    total = sum(c["valor"] for c in creditos)
    return {"ok": True, "tem_credito": True, "total": total, "creditos": creditos}


def aplicar_credito(cliente_id: str, atendimento_id: str = None) -> dict:
    """
    Aplica o(s) crédito(s) ativo(s) da cliente como sinal do próximo
    agendamento (ou específico). SÓ chame depois da Tassia CONFIRMAR que quer
    aplicar. Se o crédito sobrar (maior que o valor do serviço), devolve aviso
    pra Tassia decidir o que fazer com a sobra (não decide sozinho).
    """
    db = get_client()
    if atendimento_id:
        resp = db.table("atendimentos").select("id, valor, servico").eq("id", atendimento_id).execute()
        ag = resp.data[0] if resp.data else None
    else:
        ag = _proximo_agendamento(cliente_id)
    if not ag:
        return {"ok": False, "erro": "nenhum agendamento futuro pra aplicar o crédito"}

    cred = db.rpc("creditos_ativos_cliente", {"p_cliente_id": cliente_id}).execute()
    creditos = cred.data or []
    if not creditos:
        return {"ok": False, "erro": "essa cliente não tem crédito ativo"}

    total_credito = sum(float(c["valor"]) for c in creditos)
    total_servico = float(ag.get("valor") or 0)

    # aplica os créditos como sinal
    for c in creditos:
        db.table("pagamentos").insert({
            "atendimento_id": ag["id"], "tipo": "sinal", "valor": float(c["valor"]),
            "data": date.today().isoformat(),
        }).execute()
        db.table("creditos_cliente").update({
            "status": "aplicado", "aplicado_atendimento_id": ag["id"],
            "atualizado_em": "now()",
        }).eq("id", c["id"]).execute()

    resultado = {"ok": True, "credito_aplicado": total_credito,
                 "atendimento_id": ag["id"], "servico": ag.get("servico"),
                 "total_servico": total_servico}
    if total_servico > 0 and total_credito > total_servico:
        resultado["sobra"] = total_credito - total_servico
        resultado["aviso_sobra"] = (
            f"o crédito (R${total_credito:.2f}) é maior que o serviço "
            f"(R${total_servico:.2f}), sobrou R${total_credito - total_servico:.2f}. "
            f"O que faz com a sobra? (guardar de novo como crédito ou outra coisa)"
        )
    return resultado
