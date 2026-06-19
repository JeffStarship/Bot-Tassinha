"""
Ferramentas de métricas: faturamento, ticket médio, no-show rate,
taxa de retorno, mix por canal de aquisição.

Toda a agregação acontece no Postgres (funções RPC em db/funcoes_sessao3.sql).
Estas funções só chamam db.rpc(...) e transportam o número pronto.
Princípio do projeto: o número nasce do SQL, nunca de cálculo em Python.

Convenção de período: as funções recebem mes_referencia opcional no formato
'YYYY-MM'. Se omitido, usam o mês corrente (timezone do projeto).
"""
import os
from datetime import date
from calendar import monthrange

from database import get_client


def _bounds_mes(mes_referencia: str = None) -> tuple[str, str]:
    """
    Retorna (primeiro_dia, ultimo_dia) do mês como strings YYYY-MM-DD.
    mes_referencia no formato 'YYYY-MM'. Se None, usa o mês atual.
    """
    if mes_referencia:
        ano, mes = (int(x) for x in mes_referencia.split("-"))
    else:
        hoje = date.today()
        ano, mes = hoje.year, hoje.month
    ultimo_dia = monthrange(ano, mes)[1]
    return f"{ano:04d}-{mes:02d}-01", f"{ano:04d}-{mes:02d}-{ultimo_dia:02d}"


def faturamento(mes_referencia: str = None) -> dict:
    """
    Faturamento, quantidade de atendimentos concluídos e ticket médio
    de um mês. mes_referencia no formato 'YYYY-MM' (default: mês atual).
    """
    db = get_client()
    inicio, fim = _bounds_mes(mes_referencia)
    resp = db.rpc("metrica_faturamento", {"p_inicio": inicio, "p_fim": fim}).execute()
    if not resp.data:
        return {"periodo": f"{inicio} a {fim}", "faturamento": 0, "qtd_atendimentos": 0, "ticket_medio": 0}
    r = resp.data[0]
    return {
        "periodo": f"{inicio} a {fim}",
        "faturamento": float(r["faturamento"]),
        "qtd_atendimentos": int(r["qtd_atendimentos"]),
        "ticket_medio": float(r["ticket_medio"]),
    }


def no_show_rate(mes_referencia: str = None) -> dict:
    """
    Taxa de no-show de um mês. Base = atendimentos concluídos + no-shows
    (os que deveriam ter acontecido). mes_referencia 'YYYY-MM' (default: atual).
    """
    db = get_client()
    inicio, fim = _bounds_mes(mes_referencia)
    resp = db.rpc("metrica_no_show", {"p_inicio": inicio, "p_fim": fim}).execute()
    if not resp.data:
        return {"periodo": f"{inicio} a {fim}", "no_shows": 0, "base": 0, "taxa_pct": 0}
    r = resp.data[0]
    base = int(r["base"])
    if base == 0:
        return {"periodo": f"{inicio} a {fim}", "no_shows": 0, "base": 0, "taxa_pct": 0,
                "obs": "sem atendimentos no período"}
    return {
        "periodo": f"{inicio} a {fim}",
        "no_shows": int(r["no_shows"]),
        "base": base,
        "taxa_pct": float(r["taxa_pct"]),
    }


def mix_canal() -> dict:
    """
    Distribuição da base de clientes por canal de aquisição, com
    quantidade e participação percentual. Visão estrutural (toda a base).
    """
    db = get_client()
    resp = db.rpc("metrica_mix_canal", {}).execute()
    canais = [
        {"canal": r["canal"], "qtd": int(r["qtd"]), "pct": float(r["pct"])}
        for r in (resp.data or [])
    ]
    if not canais:
        return {"total_clientes": 0, "canais": [], "obs": "sem clientes cadastradas"}
    total = sum(c["qtd"] for c in canais)
    return {"total_clientes": total, "canais": canais}


def taxa_retorno() -> dict:
    """
    Taxa de retorno: % de clientes que voltaram (2+ atendimentos concluídos)
    sobre as que tiveram ao menos 1. É a métrica central do negócio recorrente.
    """
    db = get_client()
    resp = db.rpc("metrica_taxa_retorno", {}).execute()
    if not resp.data:
        return {"clientes_com_atendimento": 0, "clientes_que_retornaram": 0,
                "taxa_pct": 0, "obs": "sem dados"}
    r = resp.data[0]
    com = int(r["clientes_com_atendimento"])
    if com == 0:
        return {"clientes_com_atendimento": 0, "clientes_que_retornaram": 0,
                "taxa_pct": 0, "obs": "nenhuma cliente com atendimento ainda"}
    return {
        "clientes_com_atendimento": com,
        "clientes_que_retornaram": int(r["clientes_que_retornaram"]),
        "taxa_pct": float(r["taxa_pct"]),
    }
