"""
Ferramentas de atendimento: registrar atendimento concluído,
agendar futuro, listar agenda, marcar no-show/cancelamento.
"""
from database import get_client


def registrar_atendimento(
    cliente_id: str,
    data: str,
    servico: str,
    valor: float,
    hora: str = None,
    duracao_min: int = None,
    design: str = None,
    notas: str = None,
    status: str = "concluido",
) -> dict:
    """
    Registra um atendimento. data no formato YYYY-MM-DD.
    hora no formato HH:MM (opcional).
    status: agendado, concluido, no_show, cancelado
    """
    db = get_client()
    payload = {
        "cliente_id": cliente_id,
        "data": data,
        "hora": hora,
        "servico": servico,
        "valor": valor,
        "duracao_min": duracao_min,
        "design": design,
        "notas": notas,
        "status": status,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    resp = db.table("atendimentos").insert(payload).execute()

    # Se for o primeiro atendimento da cliente, marca primeira_visita e status ativa
    cliente = db.table("clientes").select("primeira_visita").eq("id", cliente_id).execute()
    if cliente.data and not cliente.data[0].get("primeira_visita"):
        db.table("clientes").update({
            "primeira_visita": data,
            "status": "ativa",
        }).eq("id", cliente_id).execute()

    return {"registrado": True, "atendimento": resp.data[0]}


def agendar(cliente_id: str, data: str, hora: str = None, servico: str = None,
            valor: float = None, duracao_min: int = None) -> dict:
    """
    Agenda um atendimento futuro (status = agendado).
    data: YYYY-MM-DD. hora: HH:MM (recomendado, pros lembretes funcionarem).
    Se valor/duracao não vierem mas o serviço estiver no catálogo, o agente
    deve puxar via buscar_preco_servico antes de chamar aqui.
    """
    db = get_client()
    payload = {
        "cliente_id": cliente_id,
        "data": data,
        "hora": hora,
        "servico": servico or "a definir",
        "valor": valor if valor is not None else 0,
        "duracao_min": duracao_min,
        "status": "agendado",
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    resp = db.table("atendimentos").insert(payload).execute()

    # Ao agendar um novo horário, limpa qualquer pendência de reagendamento
    # dessa cliente — ela acabou de ser reagendada, não deve mais aparecer no
    # relatório como "precisa reagendar".
    try:
        db.table("atendimentos").update({"precisa_reagendar": False}) \
            .eq("cliente_id", cliente_id).eq("precisa_reagendar", True).execute()
    except Exception:
        pass

    return {"agendado": True, "atendimento": resp.data[0]}


def listar_agenda(data_inicio: str, data_fim: str = None) -> dict:
    """
    Lista agendamentos num período. Se data_fim não informado,
    busca só o dia de data_inicio.
    """
    db = get_client()
    data_fim = data_fim or data_inicio
    resp = (
        db.table("atendimentos")
        .select("*, clientes(nome, telefone)")
        .gte("data", data_inicio)
        .lte("data", data_fim)
        .eq("status", "agendado")
        .order("data")
        .execute()
    )
    return {"total": len(resp.data), "agendamentos": resp.data}


def atualizar_status_atendimento(atendimento_id: str, status: str) -> dict:
    """status: agendado, concluido, no_show, cancelado"""
    db = get_client()
    resp = (
        db.table("atendimentos")
        .update({"status": status})
        .eq("id", atendimento_id)
        .execute()
    )
    if not resp.data:
        return {"atualizado": False, "erro": "atendimento não encontrado"}
    return {"atualizado": True, "atendimento": resp.data[0]}


def ajustar_inicio_atendimento(
    cliente_id: str = None,
    atendimento_id: str = None,
    ainda_nao_comecou: bool = False,
    hora_inicio: str = None,
) -> dict:
    """
    Ajusta o início real de um atendimento agendado pra HOJE, pros lembretes
    de início/fim caírem na hora certa quando há atraso.

    Use quando a Tassia disser coisas como:
    - "a Bruna ainda não começou" -> ainda_nao_comecou=True (segura o lembrete)
    - "a Bruna começou agora"      -> hora_inicio com a hora atual
    - "a Bruna começou às 15h"     -> hora_inicio="15:00"

    Identifique o atendimento por cliente_id (busca o agendamento de hoje dessa
    cliente) ou diretamente por atendimento_id. hora_inicio no formato HH:MM.
    """
    from datetime import date
    db = get_client()

    # Localiza o atendimento de hoje
    if not atendimento_id:
        if not cliente_id:
            return {"ok": False, "erro": "preciso saber de qual cliente é o atendimento"}
        hoje = date.today().isoformat()
        resp = (
            db.table("atendimentos")
            .select("id, hora, servico")
            .eq("cliente_id", cliente_id)
            .eq("data", hoje)
            .eq("status", "agendado")
            .order("hora")
            .execute()
        )
        if not resp.data:
            return {"ok": False, "erro": "nenhum atendimento agendado pra hoje dessa cliente"}
        if len(resp.data) > 1:
            return {"ok": False, "erro": "essa cliente tem mais de um atendimento hoje — especifique qual"}
        atendimento_id = resp.data[0]["id"]

    if ainda_nao_comecou:
        db.table("atendimentos").update({
            "inicio_segurado": True,
            "avisado_inicio": False,
        }).eq("id", atendimento_id).execute()
        return {"ok": True, "acao": "segurado",
                "msg": "lembrete de início segurado — me avise quando começar de fato"}

    if hora_inicio:
        # libera o segurado, grava o início real e reabre os avisos pra recontar
        from datetime import datetime
        hoje = date.today().isoformat()
        inicio_real_ts = f"{hoje}T{hora_inicio}:00-03:00"  # offset SP
        db.table("atendimentos").update({
            "inicio_real": inicio_real_ts,
            "inicio_segurado": False,
            "avisado_inicio": True,   # início já reconhecido pela Tassia
            "avisado_fim": False,     # recontar o fim a partir do início real
        }).eq("id", atendimento_id).execute()
        return {"ok": True, "acao": "inicio_ajustado", "hora_inicio": hora_inicio}

    return {"ok": False, "erro": "diga se ainda não começou ou a que horas começou"}


def remarcar_atendimento(cliente_id: str, nova_data: str, nova_hora: str = None) -> dict:
    """
    Remarca (muda a data/hora) do próximo atendimento agendado de uma cliente,
    SEM cancelar nem criar outro. Use quando a Tassia disser "muda o horário da
    Bruna pra tal dia", "passa a Bruna pra sexta", "remarca a Bruna".

    Importante: remarcar NÃO é cancelar. Isso mantém as métricas limpas — não
    conta como cancelamento nem infla a contagem de agendamentos. É a mesma
    reserva mudando de dia.

    nova_data: YYYY-MM-DD. nova_hora: HH:MM (opcional; se não vier, mantém a
    hora atual). Remarca o próximo agendamento futuro da cliente.
    """
    from datetime import date
    db = get_client()
    hoje = date.today().isoformat()
    resp = (
        db.table("atendimentos")
        .select("id, data, hora, servico")
        .eq("cliente_id", cliente_id)
        .eq("status", "agendado")
        .gte("data", hoje)
        .order("data")
        .execute()
    )
    if not resp.data:
        return {"ok": False, "erro": "nenhum atendimento agendado futuro pra essa cliente"}

    ag = resp.data[0]
    update = {
        "data": nova_data,
        "precisa_reagendar": False,  # remarcou -> não é mais pendência
        # zera as flags de lembrete já enviado, pros lembretes valerem pro novo horário
        "avisado_followup_1d": False,
        "avisado_followup_3h": False,
    }
    if nova_hora is not None:
        update["hora"] = nova_hora

    db.table("atendimentos").update(update).eq("id", ag["id"]).execute()

    return {
        "ok": True,
        "remarcado": True,
        "de": {"data": ag["data"], "hora": ag["hora"]},
        "para": {"data": nova_data, "hora": nova_hora or ag["hora"]},
        "servico": ag.get("servico"),
    }


def marcar_cancelamento(cliente_id: str, reagendou: bool = False) -> dict:
    """
    Marca o atendimento agendado de uma cliente como cancelado.
    Use quando a Tassia disser "a Bruna cancelou".
    - reagendou=False: marca precisa_reagendar=True (aparece no diário das 9h
      até reagendar ou a Tassia mandar parar de avisar).
    - reagendou=True: só cancela, sem entrar na lista de pendência (a Tassia
      vai agendar o novo horário em seguida).
    Cancela o próximo agendamento futuro da cliente.
    """
    from datetime import date
    db = get_client()
    hoje = date.today().isoformat()
    resp = (
        db.table("atendimentos")
        .select("id, data, hora")
        .eq("cliente_id", cliente_id)
        .eq("status", "agendado")
        .gte("data", hoje)
        .order("data")
        .execute()
    )
    if not resp.data:
        return {"ok": False, "erro": "nenhum atendimento agendado futuro pra essa cliente"}
    atendimento_id = resp.data[0]["id"]
    db.table("atendimentos").update({
        "status": "cancelado",
        "precisa_reagendar": (not reagendou),
    }).eq("id", atendimento_id).execute()
    return {"ok": True, "cancelado": True,
            "precisa_reagendar": (not reagendou)}


def parar_de_avisar_reagendar(cliente_id: str) -> dict:
    """
    Para de mostrar uma cliente cancelada na lista de 'precisa reagendar' do
    resumo diário. Use quando a Tassia disser algo como "pode parar de avisar
    da Bruna" / "não precisa mais lembrar de reagendar a Bruna".
    """
    db = get_client()
    resp = (
        db.table("atendimentos")
        .update({"precisa_reagendar": False})
        .eq("cliente_id", cliente_id)
        .eq("precisa_reagendar", True)
        .execute()
    )
    n = len(resp.data) if resp.data else 0
    return {"ok": True, "limpos": n}
