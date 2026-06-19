"""
Ferramentas de preferências por cliente (Sessão 9).

Tabela genérica chave-valor (preferencias_cliente) — preparada pra crescer.
Hoje cobre follow-up: ligar/desligar, texto e antecedência customizados por
cliente. Ausência de preferência = usa o padrão global.

Chaves de follow-up:
  followup_ativo    -> 'true' | 'false'
  followup_1d_texto -> texto custom do lembrete de dias antes
  followup_3h_texto -> texto custom do lembrete de horas antes
  followup_1d_dias  -> quantos dias antes (default 1)
  followup_3h_horas -> quantas horas antes (default 3)
"""
from database import get_client

CHAVES_FOLLOWUP = {
    "followup_ativo", "followup_1d_texto", "followup_3h_texto",
    "followup_1d_dias", "followup_3h_horas",
}


def definir_followup_cliente(
    cliente_id: str,
    ativo: bool = None,
    texto_dias_antes: str = None,
    texto_horas_antes: str = None,
    dias_antes: int = None,
    horas_antes: int = None,
) -> dict:
    """
    Customiza o follow-up de UMA cliente específica. Só os campos informados
    são alterados; os demais continuam como estão (ou no padrão global).

    - ativo: False desliga os follow-ups dessa cliente; True religa.
    - texto_dias_antes: mensagem custom do lembrete de dias antes.
    - texto_horas_antes: mensagem custom do lembrete de horas antes.
    - dias_antes: quantos dias antes mandar o 1º lembrete (padrão 1).
    - horas_antes: quantas horas antes mandar o 2º lembrete (padrão 3).

    Sempre confirme com a Tassia o que vai mudar ANTES de chamar esta tool.
    Use {nome} e {hora} nos textos pra serem preenchidos automaticamente.
    """
    db = get_client()
    mudancas = []

    def _set(chave, valor):
        db.rpc("definir_preferencia", {
            "p_cliente_id": cliente_id, "p_chave": chave, "p_valor": str(valor)
        }).execute()
        mudancas.append(chave)

    if ativo is not None:
        _set("followup_ativo", "true" if ativo else "false")
    if texto_dias_antes is not None:
        _set("followup_1d_texto", texto_dias_antes)
    if texto_horas_antes is not None:
        _set("followup_3h_texto", texto_horas_antes)
    if dias_antes is not None:
        _set("followup_1d_dias", int(dias_antes))
    if horas_antes is not None:
        _set("followup_3h_horas", int(horas_antes))

    if not mudancas:
        return {"ok": False, "erro": "nada pra alterar — diga o que customizar"}
    return {"ok": True, "alterado": mudancas}


def resetar_followup_cliente(cliente_id: str) -> dict:
    """
    Remove todas as customizações de follow-up de uma cliente — ela volta a
    usar o padrão global. Use quando a Tassia disser 'volta a Bruna pro padrão'.
    """
    db = get_client()
    db.rpc("resetar_followup_cliente", {"p_cliente_id": cliente_id}).execute()
    return {"ok": True, "msg": "follow-up da cliente voltou ao padrão"}


def ver_followup_cliente(cliente_id: str) -> dict:
    """
    Mostra as customizações de follow-up de uma cliente. Se não tiver
    nenhuma, ela usa o padrão global.
    """
    db = get_client()
    resp = db.rpc("preferencias_de", {"p_cliente_id": cliente_id}).execute()
    prefs = {r["chave"]: r["valor"] for r in (resp.data or [])
             if r["chave"] in CHAVES_FOLLOWUP}
    if not prefs:
        return {"customizado": False, "obs": "essa cliente usa o follow-up padrão"}
    return {"customizado": True, "preferencias": prefs}
