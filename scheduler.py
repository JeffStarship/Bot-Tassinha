"""
Scheduler do Bot Tassinha (Sessão 4).

Roda num container separado do bot. Dispara resumos automáticos no Telegram
pros IDs da whitelist (mesma env var do bot — TELEGRAM_ALLOWED_USER_IDS):

  - DIÁRIO   (seg a sáb, 9h):  números do mês até agora, risco do dia,
                               aniversariantes (hoje + amanhã).
  - SEMANAL  (domingo, 9h):    semana fechada (seg-sáb) vs. semana anterior,
                               + dica do consultor se caiu.
  - MENSAL   (dia 1º, 9h):     mês anterior cheio vs. mês retrasado,
                               + dica do consultor se caiu.

Princípios herdados do projeto:
  - Todo número vem das tools (que vêm do SQL). O scheduler só monta texto.
  - Seção sem dado some do resumo (nada de "nenhuma cliente em risco").
  - Conselho só via consultor (Sonnet), e só quando há queda.
  - Texto puro pra Tassinha: sem markdown, sem id.

Sobreposição de calendário:
  - Domingo normal      -> semanal (o diário NÃO roda domingo)
  - Seg a sáb normal     -> diário
  - Dia 1º (qualquer dia) -> mensal, ADICIONAL ao diário/semanal daquele dia
  Cada job é independente e checa sua própria condição de data, então a
  sobreposição acontece naturalmente sem coordenação entre eles.
"""
import os
import logging
from datetime import date, datetime, timedelta
from calendar import monthrange

import pytz
import asyncio
from telegram import Bot
from apscheduler.schedulers.blocking import BlockingScheduler

from tools import metricas, risco, financeiro
from tools.atendimentos import get_client  # reusa o singleton supabase
import consultor

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("scheduler")

TZ = pytz.timezone(os.environ.get("TIMEZONE", "America/Sao_Paulo"))
_MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
          "agosto", "setembro", "outubro", "novembro", "dezembro"]
_DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
         "sexta-feira", "sábado", "domingo"]


def _hoje() -> date:
    return datetime.now(TZ).date()


def _ids() -> list[int]:
    raw = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "")
    return [int(x) for x in raw.split(",") if x.strip()]


def _enviar(texto: str, botao_texto: str = None, botao_url: str = None) -> None:
    """
    Envia o texto pra todos os IDs da whitelist.
    Se botao_texto e botao_url vierem, anexa um botão inline (ex: abrir wa.me).
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    bot = Bot(token=token)
    ids = _ids()
    if not ids:
        logger.warning("Nenhum ID na whitelist — mensagem não enviada.")
        return

    markup = None
    if botao_texto and botao_url:
        markup = InlineKeyboardMarkup([[InlineKeyboardButton(botao_texto, url=botao_url)]])

    async def _go():
        for uid in ids:
            try:
                await bot.send_message(chat_id=uid, text=texto, reply_markup=markup)
                logger.info(f"Mensagem enviada para {uid}")
            except Exception:
                logger.exception(f"Falha ao enviar para {uid}")

    asyncio.run(_go())


def _link_whatsapp(telefone: str, mensagem: str) -> str:
    """Monta o deep link wa.me com a mensagem pré-preenchida (URL-encoded)."""
    from urllib.parse import quote
    return f"https://wa.me/{telefone}?text={quote(mensagem)}"


def _fmt_reais(v: float) -> str:
    # 1234.5 -> "R$ 1.234,50"
    s = f"{v:,.2f}"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def _variacao(atual: float, anterior: float) -> str:
    """Frase de variação honesta. Trata divisão por zero."""
    if anterior == 0 and atual == 0:
        return "estável (sem movimento nos dois períodos)"
    if anterior == 0:
        return "subiu (não havia base no período anterior pra comparar %)"
    pct = (atual - anterior) / anterior * 100
    if abs(pct) < 0.5:
        return "praticamente estável"
    direcao = "subiu" if pct > 0 else "caiu"
    return f"{direcao} {abs(pct):.0f}%"


def _caiu(atual: float, anterior: float) -> bool:
    return anterior > 0 and atual < anterior


# ---------------------------------------------------------------------------
# DIÁRIO
# ---------------------------------------------------------------------------

def montar_diario() -> str:
    hoje = _hoje()
    linhas = [f"Bom dia! Resumo de {_DIAS[hoje.weekday()]}, {hoje.strftime('%d/%m')}.", ""]

    # Faturamento do mês até agora + saldo
    try:
        s = financeiro.saldo_mes()
        if "saldo" in s:
            linhas.append(
                f"Faturamento do mês até agora: {_fmt_reais(s['faturamento'])}. "
                f"Saldo: {_fmt_reais(s['saldo'])}."
            )
    except Exception:
        logger.exception("diario: saldo falhou")

    # Clientes em risco hoje
    try:
        r = risco.clientes_em_risco()
        if r["total"] > 0:
            nomes = "\n".join(
                f"- {c['nome']}: {c['dias_desde_ultimo']} dias sem voltar"
                for c in r["clientes"][:10]
            )
            linhas.append("")
            linhas.append(f"Clientes em risco ({r['total']}):")
            linhas.append(nomes)
    except Exception:
        logger.exception("diario: risco falhou")

    # Aniversariantes hoje + amanhã
    aniv = _aniversariantes_texto(1)
    if aniv:
        linhas.append("")
        linhas.append(aniv)

    # Clientes que cancelaram e precisam reagendar
    try:
        db = get_client()
        resp = db.rpc("pendentes_reagendar", {}).execute()
        pend = resp.data or []
        if pend:
            linhas.append("")
            linhas.append(f"Falta reagendar ({len(pend)}):")
            linhas.append("\n".join(
                f"- {p['nome']} (cancelou {p['servico']})" for p in pend[:10]
            ))
    except Exception:
        logger.exception("diario: pendentes_reagendar falhou")

    return "\n".join(linhas)


def _aniversariantes_texto(dias_a_frente: int) -> str | None:
    try:
        db = get_client()
        resp = db.rpc("aniversariantes", {"p_dias_a_frente": dias_a_frente}).execute()
        dados = resp.data or []
    except Exception:
        logger.exception("aniversariantes falhou")
        return None
    if not dados:
        return None
    hoje_l = [d["nome"] for d in dados if d["quando"] == "hoje"]
    amanha_l = [d["nome"] for d in dados if d["quando"] == "amanhã"]
    partes = []
    if hoje_l:
        partes.append(f"Faz aniversário hoje: {', '.join(hoje_l)}")
    if amanha_l:
        partes.append(f"Faz aniversário amanhã: {', '.join(amanha_l)}")
    return "\n".join(partes) if partes else None


# ---------------------------------------------------------------------------
# SEMANAL (domingo) — semana fechada seg-sáb vs. semana anterior
# ---------------------------------------------------------------------------

def montar_semanal() -> str:
    hoje = _hoje()  # domingo
    # semana que fechou: segunda (hoje-6) a sábado (hoje-1)
    sab = hoje - timedelta(days=1)
    seg = hoje - timedelta(days=6)
    # semana anterior: seg-7 a sab-7
    seg_ant = seg - timedelta(days=7)
    sab_ant = sab - timedelta(days=7)

    db = get_client()
    atual = db.rpc("atendimentos_periodo",
                   {"p_inicio": seg.isoformat(), "p_fim": sab.isoformat()}).execute().data[0]
    ant = db.rpc("atendimentos_periodo",
                 {"p_inicio": seg_ant.isoformat(), "p_fim": sab_ant.isoformat()}).execute().data[0]

    fat_a, fat_b = float(atual["faturamento"]), float(ant["faturamento"])
    qtd_a, qtd_b = int(atual["qtd"]), int(ant["qtd"])

    linhas = [
        f"Resumo da semana ({seg.strftime('%d/%m')} a {sab.strftime('%d/%m')}).",
        "",
        f"Faturamento: {_fmt_reais(fat_a)} ({_variacao(fat_a, fat_b)} vs. semana anterior).",
        f"Atendimentos: {qtd_a} ({_variacao(qtd_a, qtd_b)} vs. semana anterior).",
    ]

    # Risco (lista completa)
    try:
        r = risco.clientes_em_risco()
        if r["total"] > 0:
            linhas.append("")
            linhas.append(f"Clientes em risco ({r['total']}):")
            linhas.append("\n".join(
                f"- {c['nome']}: {c['dias_desde_ultimo']} dias" for c in r["clientes"][:15]
            ))
    except Exception:
        logger.exception("semanal: risco falhou")

    # Dica do consultor só se caiu (faturamento OU atendimentos)
    if _caiu(fat_a, fat_b) or _caiu(qtd_a, qtd_b):
        dica = _dica_consultor(
            f"Na semana de {seg.strftime('%d/%m')} a {sab.strftime('%d/%m')}, "
            f"o faturamento foi {_fmt_reais(fat_a)} (semana anterior {_fmt_reais(fat_b)}) "
            f"e foram {qtd_a} atendimentos (anterior {qtd_b}). "
            f"Houve queda. Em até 3 frases curtas e práticas, o que a Tassinha "
            f"pode fazer essa semana pra reverter? Seja específico e acionável."
        )
        if dica:
            linhas.append("")
            linhas.append("Dica da semana:")
            linhas.append(dica)

    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# MENSAL (dia 1º) — mês anterior cheio vs. mês retrasado
# ---------------------------------------------------------------------------

def montar_mensal() -> str:
    hoje = _hoje()  # dia 1
    # mês anterior
    fim_ant = hoje - timedelta(days=1)            # último dia do mês passado
    ini_ant = fim_ant.replace(day=1)
    # mês retrasado
    fim_retr = ini_ant - timedelta(days=1)
    ini_retr = fim_retr.replace(day=1)

    db = get_client()
    a = db.rpc("atendimentos_periodo",
               {"p_inicio": ini_ant.isoformat(), "p_fim": fim_ant.isoformat()}).execute().data[0]
    b = db.rpc("atendimentos_periodo",
               {"p_inicio": ini_retr.isoformat(), "p_fim": fim_retr.isoformat()}).execute().data[0]

    fat_a, fat_b = float(a["faturamento"]), float(b["faturamento"])
    qtd_a, qtd_b = int(a["qtd"]), int(b["qtd"])
    nome_mes = _MESES[ini_ant.month - 1]

    linhas = [
        f"Fechamento de {nome_mes}.",
        "",
        f"Faturamento: {_fmt_reais(fat_a)} ({_variacao(fat_a, fat_b)} vs. mês anterior).",
        f"Atendimentos: {qtd_a} ({_variacao(qtd_a, qtd_b)} vs. mês anterior).",
    ]

    # Métricas estruturais do mês fechado
    try:
        t = metricas.taxa_retorno()
        if t.get("clientes_com_atendimento", 0) > 0:
            linhas.append(f"Taxa de retorno: {t['taxa_pct']}%.")
    except Exception:
        logger.exception("mensal: taxa_retorno falhou")

    if _caiu(fat_a, fat_b) or _caiu(qtd_a, qtd_b):
        dica = _dica_consultor(
            f"O mês de {nome_mes} fechou com faturamento {_fmt_reais(fat_a)} "
            f"(mês anterior {_fmt_reais(fat_b)}) e {qtd_a} atendimentos "
            f"(anterior {qtd_b}). Houve queda no fechamento mensal. Em até 4 "
            f"frases, dê um plano prático pra Tassinha recuperar no próximo mês."
        )
        if dica:
            linhas.append("")
            linhas.append("Plano pro mês que vem:")
            linhas.append(dica)

    return "\n".join(linhas)


def _dica_consultor(pergunta: str) -> str | None:
    try:
        return consultor.consultar(pergunta)
    except Exception:
        logger.exception("dica do consultor falhou")
        return None


# ---------------------------------------------------------------------------
# JOBS — cada um checa sua própria condição de data
# ---------------------------------------------------------------------------

def job_diario():
    hoje = _hoje()
    if hoje.weekday() == 6:  # domingo (0=segunda) — diário não roda domingo
        logger.info("Domingo: diário não roda (semanal cobre).")
        return
    logger.info("Disparando resumo DIÁRIO.")
    try:
        _enviar(montar_diario())
    except Exception:
        logger.exception("job_diario falhou")


def job_semanal():
    if _hoje().weekday() != 6:  # só domingo
        return
    logger.info("Disparando resumo SEMANAL.")
    try:
        _enviar(montar_semanal())
    except Exception:
        logger.exception("job_semanal falhou")


def job_mensal():
    if _hoje().day != 1:  # só dia 1º
        return
    logger.info("Disparando resumo MENSAL.")
    try:
        _enviar(montar_mensal())
    except Exception:
        logger.exception("job_mensal falhou")


def _eh_manutencao(servico: str) -> bool:
    return "manuten" in (servico or "").lower()


def montar_msg_inicio(nome: str, servico: str) -> str:
    return f"O atendimento da {nome} está começando agora ({servico})."


def montar_msg_fim(nome: str, servico: str) -> str:
    if _eh_manutencao(servico):
        proximo = "já deixe o próximo alongamento agendado"
    else:
        proximo = "já deixe a manutenção agendada"
    return (
        f"O atendimento da {nome} está quase acabando. Antes dela ir embora:\n"
        f"- {proximo[0].upper()}{proximo[1:]}\n"
        f"- Comente do programa de indicações com ela"
    )


def job_lembretes():
    """Roda a cada 5 min: dispara lembrete de início e de fim na hora certa."""
    agora = datetime.now(TZ)
    hoje = agora.date()
    hora_atual = agora.time()
    db = get_client()

    # COMEÇANDO
    try:
        resp = db.rpc("atendimentos_comecando", {
            "p_hoje": hoje.isoformat(),
            "p_agora": hora_atual.strftime("%H:%M:%S"),
            "p_tolerancia_min": 5,
        }).execute()
        for a in (resp.data or []):
            _enviar(montar_msg_inicio(a["nome"], a["servico"]))
            db.rpc("marcar_avisado_inicio", {"p_id": a["id"]}).execute()
            logger.info(f"Lembrete de INÍCIO enviado: {a['nome']}")
    except Exception:
        logger.exception("job_lembretes: início falhou")

    # ACABANDO
    try:
        resp = db.rpc("atendimentos_acabando", {
            "p_hoje": hoje.isoformat(),
            "p_agora_ts": agora.isoformat(),
            "p_tolerancia_min": 5,
        }).execute()
        for a in (resp.data or []):
            _enviar(montar_msg_fim(a["nome"], a["servico"]))
            db.rpc("marcar_avisado_fim", {"p_id": a["id"]}).execute()
            logger.info(f"Lembrete de FIM enviado: {a['nome']}")
    except Exception:
        logger.exception("job_lembretes: fim falhou")


def _config(chave: str, default: str = "") -> str:
    try:
        db = get_client()
        resp = db.table("config").select("valor").eq("chave", chave).execute()
        if resp.data:
            return resp.data[0]["valor"]
    except Exception:
        logger.exception(f"config {chave} falhou")
    return default


def _hora_fmt(hora_val) -> str:
    # aceita "14:00:00" (string do Supabase) ou datetime.time
    if hora_val is None:
        return ""
    s = str(hora_val)
    return s[:5]


def _pref_cliente(cliente_id: str, chave: str) -> str | None:
    """Lê uma preferência da cliente, ou None se não houver."""
    try:
        db = get_client()
        resp = db.rpc("_pref", {"p_cliente_id": cliente_id, "p_chave": chave}).execute()
        if resp.data:
            # _pref retorna scalar; supabase embrulha em lista de dict
            row = resp.data[0] if isinstance(resp.data, list) else resp.data
            if isinstance(row, dict):
                return list(row.values())[0]
            return row
    except Exception:
        logger.exception(f"_pref_cliente {chave} falhou")
    return None


def _disparar_followup(a: dict, modelo_chave: str, default_msg: str, marcar_rpc: str,
                       pref_texto_chave: str, quando_label: str):
    """
    Monta msg + botão wa.me e envia. Marca como avisado depois.
    Usa texto customizado da cliente (se houver), senão o padrão global.
    quando_label: texto pronto de "quando" (ex: 'amanhã', 'daqui a 2 dias',
    'hoje') já calculado pelo job conforme a preferência da cliente.
    """
    db = get_client()
    nome = a["nome"]
    primeiro_nome = nome.split()[0] if nome else nome
    hora = _hora_fmt(a["hora"])
    tel = a.get("telefone")

    # texto: preferência da cliente > padrão global > default
    template = _pref_cliente(a["cliente_id"], pref_texto_chave) or _config(modelo_chave, default_msg)
    msg_cliente = template.replace("{nome}", nome).replace("{hora}", hora)

    if tel:
        texto = f"Lembrete: {nome} tem horário {quando_label} às {hora}.\nMensagem pronta no botão abaixo."
        link = _link_whatsapp(tel, msg_cliente)
        _enviar(texto, botao_texto=f"Mandar lembrete WhatsApp {primeiro_nome}", botao_url=link)
    else:
        texto = (f"Lembrete: {nome} tem horário {quando_label} às {hora}, mas não tem "
                 f"telefone cadastrado. Cadastre o número da {primeiro_nome} pra "
                 f"ativar o botão de WhatsApp nos próximos lembretes.")
        _enviar(texto)

    db.rpc(marcar_rpc, {"p_id": a["id"]}).execute()
    logger.info(f"Lembrete ({modelo_chave}) enviado: {nome}")


def job_followup_1dia():
    """Roda 1x de manhã: follow-up das clientes agendadas (dias antes configurável)."""
    hoje = _hoje()
    db = get_client()
    try:
        resp = db.rpc("followups_1dia", {"p_hoje": hoje.isoformat()}).execute()
        for a in (resp.data or []):
            dias = int(a.get("dias_antes") or 1)
            quando = "amanhã" if dias == 1 else f"daqui a {dias} dias"
            _disparar_followup(a, "followup_1d_msg",
                               "Oi {nome}! Passando pra confirmar seu horário amanhã às {hora}. Posso confirmar?",
                               "marcar_followup_1d", "followup_1d_texto", quando)
    except Exception:
        logger.exception("job_followup_1dia falhou")


def job_followup_3horas():
    """Roda a cada 5 min: follow-up N horas antes (configurável por cliente)."""
    agora = datetime.now(TZ)
    hoje = agora.date()
    db = get_client()
    try:
        resp = db.rpc("followups_3horas", {
            "p_hoje": hoje.isoformat(),
            "p_agora": agora.time().strftime("%H:%M:%S"),
            "p_tolerancia_min": 5,
        }).execute()
        for a in (resp.data or []):
            horas = int(a.get("horas_antes") or 3)
            quando = "hoje"  # é sempre no mesmo dia
            _disparar_followup(a, "followup_3h_msg",
                               "Oi {nome}! Lembrete do seu horário hoje às {hora}. Está tudo certo?",
                               "marcar_followup_3h", "followup_3h_texto", quando)
    except Exception:
        logger.exception("job_followup_3horas falhou")


def main():
    sched = BlockingScheduler(timezone=TZ)
    # Resumos: disparam 9h; cada um filtra o próprio dia internamente.
    sched.add_job(job_diario, "cron", hour=9, minute=0, id="diario")
    sched.add_job(job_semanal, "cron", hour=9, minute=0, id="semanal")
    sched.add_job(job_mensal, "cron", hour=9, minute=0, id="mensal")
    # Lembretes de início/fim: verifica a cada 5 minutos ao longo do dia.
    sched.add_job(job_lembretes, "interval", minutes=5, id="lembretes")
    # Follow-up de 1 dia antes: 1x de manhã (8h).
    sched.add_job(job_followup_1dia, "cron", hour=8, minute=0, id="followup_1dia")
    # Follow-up de 3h antes: verifica a cada 5 minutos.
    sched.add_job(job_followup_3horas, "interval", minutes=5, id="followup_3h")
    logger.info("Scheduler Tassinha iniciado (resumos 9h + lembretes 5min + follow-ups — SP).")
    sched.start()


if __name__ == "__main__":
    main()
