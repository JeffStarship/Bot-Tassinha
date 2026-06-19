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
