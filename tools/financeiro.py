"""
Ferramentas financeiras: registrar despesa (pontual ou recorrente),
listar despesas recorrentes ativas, saldo do mês (faturamento - despesas).

Registros (insert) usam supabase-py direto, no mesmo padrão das outras
tools de escrita. Agregações (despesas do período, saldo) usam RPC —
o número nasce do SQL.
"""
from datetime import date
from calendar import monthrange

from database import get_client

CATEGORIAS = ("aluguel", "material", "marketing", "transporte", "contas", "outros")


def _bounds_mes(mes_referencia: str = None) -> tuple[str, str]:
    if mes_referencia:
        ano, mes = (int(x) for x in mes_referencia.split("-"))
    else:
        hoje = date.today()
        ano, mes = hoje.year, hoje.month
    ultimo = monthrange(ano, mes)[1]
    return f"{ano:04d}-{mes:02d}-01", f"{ano:04d}-{mes:02d}-{ultimo:02d}"


def registrar_despesa(
    descricao: str,
    valor: float,
    categoria: str = "outros",
    data: str = None,
    recorrente: bool = False,
    dia_recorrencia: int = None,
) -> dict:
    """
    Registra uma despesa.
    - categoria: aluguel, material, marketing, transporte, contas, outros
    - data: YYYY-MM-DD (default: hoje). Para recorrente, é a data de início.
    - recorrente: True pra despesa fixa mensal (ex: aluguel).
    - dia_recorrencia: dia do mês que repete (1-31), só pra recorrente.
    """
    db = get_client()
    if categoria not in CATEGORIAS:
        categoria = "outros"
    payload = {
        "descricao": descricao,
        "valor": valor,
        "categoria": categoria,
        "data": data or date.today().isoformat(),
        "recorrente": recorrente,
        "dia_recorrencia": dia_recorrencia,
        "ativa": True,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    resp = db.table("despesas").insert(payload).execute()
    return {"registrado": True, "despesa": resp.data[0]}


def despesas_recorrentes() -> dict:
    """
    Lista as despesas recorrentes ativas (custos fixos mensais).
    Leitura simples — é uma lista curta, não precisa de agregação.
    """
    db = get_client()
    resp = (
        db.table("despesas")
        .select("id, descricao, valor, categoria, dia_recorrencia, data")
        .eq("recorrente", True)
        .eq("ativa", True)
        .order("valor", desc=True)
        .execute()
    )
    itens = resp.data or []
    total = round(sum(float(d["valor"]) for d in itens), 2)
    if not itens:
        return {"total_mensal": 0, "despesas": [], "obs": "sem despesas recorrentes cadastradas"}
    return {"total_mensal": total, "despesas": itens}


def saldo_mes(mes_referencia: str = None) -> dict:
    """
    Saldo do mês: faturamento (atendimentos concluídos) menos despesas
    (pontuais do mês + recorrentes ativas). mes_referencia 'YYYY-MM'
    (default: mês atual). Saldo negativo = mês no vermelho.
    """
    db = get_client()
    inicio, _ = _bounds_mes(mes_referencia)
    resp = db.rpc("saldo_mes", {"p_data_no_mes": inicio}).execute()
    if not resp.data:
        return {"obs": "sem dados pra calcular saldo"}
    r = resp.data[0]
    return {
        "inicio": r["inicio"],
        "fim": r["fim"],
        "faturamento": float(r["faturamento"]),
        "despesas": float(r["despesas"]),
        "saldo": float(r["saldo"]),
    }


def despesas_do_mes(mes_referencia: str = None) -> dict:
    """
    Total de despesas de um mês, separando pontuais de recorrentes.
    mes_referencia 'YYYY-MM' (default: mês atual).
    """
    db = get_client()
    inicio, fim = _bounds_mes(mes_referencia)
    resp = db.rpc("despesas_periodo", {"p_inicio": inicio, "p_fim": fim}).execute()
    if not resp.data:
        return {"periodo": f"{inicio} a {fim}", "despesas_pontuais": 0,
                "despesas_recorrentes": 0, "total": 0}
    r = resp.data[0]
    return {
        "periodo": f"{inicio} a {fim}",
        "despesas_pontuais": float(r["despesas_pontuais"]),
        "despesas_recorrentes": float(r["despesas_recorrentes"]),
        "total": float(r["total"]),
    }
