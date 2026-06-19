"""
Ferramentas de risco e cadência: clientes em risco de sumir, cadência
individual com nível de confiança, ranking de inatividade.

Agregação no Postgres (db/funcoes_sessao3.sql). Aqui só chamamos as RPCs.

Conceitos:
- Cadência = intervalo médio (dias) entre atendimentos concluídos
  consecutivos de uma cliente.
- Confiança da cadência pela contagem de visitas:
    < 3 visitas  -> 'fraca'  (pouca base, número pouco confiável)
    3 a 5        -> 'media'
    6 ou mais    -> 'solida'
- Em risco = dias desde o último atendimento > cadência * multiplicador.
  Quem tem menos de 2 visitas (sem cadência) usa o fallback DEFAULT_RETURN_DAYS.

Parâmetros lidos do ambiente (com defaults seguros):
  AT_RISK_MULTIPLIER (default 1.3)
  DEFAULT_RETURN_DAYS (default 21)
"""
import os

from database import get_client


def _multiplicador() -> float:
    return float(os.environ.get("AT_RISK_MULTIPLIER", "1.3"))


def _dias_padrao() -> int:
    return int(os.environ.get("DEFAULT_RETURN_DAYS", "21"))


def clientes_em_risco() -> dict:
    """
    Lista clientes que passaram do tempo esperado de retorno, ordenadas
    da mais atrasada pra menos (mais urgente primeiro). Para cada uma:
    cadência individual, nível de confiança, há quantos dias não volta,
    o limite esperado e o atraso em dias.

    Clientes com status 'inativa' são excluídas (já foram dadas como perdidas).
    """
    db = get_client()
    resp = db.rpc("clientes_em_risco", {
        "p_multiplicador": _multiplicador(),
        "p_dias_padrao": _dias_padrao(),
    }).execute()

    em_risco = []
    for r in (resp.data or []):
        em_risco.append({
            "cliente_id": r["cliente_id"],
            "nome": r["nome"],
            "telefone": r["telefone"],
            "visitas": int(r["visitas"]),
            "cadencia_dias": float(r["cadencia_dias"]) if r["cadencia_dias"] is not None else None,
            "confianca": r["confianca"],
            "ultimo_atendimento": r["ultimo_atendimento"],
            "dias_desde_ultimo": int(r["dias_desde_ultimo"]),
            "limite_dias": float(r["limite_dias"]),
            "atraso_dias": float(r["atraso_dias"]),
        })

    if not em_risco:
        return {"total": 0, "clientes": [], "obs": "nenhuma cliente em risco no momento"}
    return {"total": len(em_risco), "clientes": em_risco}


def cadencia_cliente(cliente_id: str) -> dict:
    """
    Cadência individual de uma cliente: de quantos em quantos dias ela
    costuma voltar, com nível de confiança baseado no número de visitas.
    Use o id retornado por buscar_cliente.

    Com menos de 2 atendimentos não há intervalo calculável — retorna
    cadencia_dias = None e avisa que ainda não dá pra prever.
    """
    db = get_client()
    resp = db.rpc("cadencia_cliente", {"p_cliente_id": cliente_id}).execute()
    if not resp.data:
        return {"cliente_id": cliente_id, "visitas": 0, "cadencia_dias": None,
                "confianca": "fraca", "obs": "cliente sem atendimentos concluídos"}
    r = resp.data[0]
    visitas = int(r["visitas"]) if r["visitas"] is not None else 0
    cad = float(r["cadencia_dias"]) if r["cadencia_dias"] is not None else None
    out = {
        "cliente_id": cliente_id,
        "visitas": visitas,
        "cadencia_dias": cad,
        "confianca": r["confianca"],
        "ultimo_atendimento": r["ultimo_atendimento"],
        "dias_desde_ultimo": int(r["dias_desde_ultimo"]) if r["dias_desde_ultimo"] is not None else None,
    }
    if cad is None:
        out["obs"] = "menos de 2 visitas — ainda não dá pra calcular cadência"
    return out


def ranking_inatividade(limite: int = 20) -> dict:
    """
    Lista as clientes ordenadas por quanto tempo faz desde o último
    atendimento (mais inativa primeiro). Foto completa pra decidir quem
    reativar — não filtra por risco. limite = quantas trazer (default 20).
    """
    db = get_client()
    resp = db.rpc("ranking_inatividade", {"p_limite": limite}).execute()
    ranking = []
    for r in (resp.data or []):
        ranking.append({
            "cliente_id": r["cliente_id"],
            "nome": r["nome"],
            "telefone": r["telefone"],
            "ultimo_atendimento": r["ultimo_atendimento"],
            "dias_desde_ultimo": int(r["dias_desde_ultimo"]),
            "total_visitas": int(r["total_visitas"]),
        })
    if not ranking:
        return {"total": 0, "clientes": [], "obs": "sem clientes com atendimento"}
    return {"total": len(ranking), "clientes": ranking}
