"""
Camada do consultor estratégico (Sonnet).

Acionada pela ferramenta escalar_para_consultor quando a Tassinha faz uma
pergunta de análise, julgamento, causa ou conselho.

Princípio: o Sonnet NUNCA inventa número. Antes de chamar o modelo, este
módulo monta um pacote de dados REAIS do banco (faturamento, clientes em
risco, etc) e entrega como contexto. O Sonnet analisa e aconselha em cima
desses números — nunca em cima de achismo.

Se a chamada ao Sonnet falhar (cota, rede), retorna fallback gracioso.
"""
import os
import logging
from datetime import datetime
import pytz
from anthropic import Anthropic

from tools import metricas, risco, financeiro

logger = logging.getLogger(__name__)

_anthropic: Anthropic | None = None

_MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
          "agosto", "setembro", "outubro", "novembro", "dezembro"]


def _data_hoje() -> str:
    tz = pytz.timezone(os.environ.get("TIMEZONE", "America/Sao_Paulo"))
    agora = datetime.now(tz)
    return (f"Hoje é {agora.day} de {_MESES[agora.month - 1]} de {agora.year} "
            f"({agora.strftime('%d/%m/%Y')}).")


# Reforço de formatação: a resposta do consultor vai direto pra Tassinha via
# Telegram, então não pode ter markdown nem id técnico.
_REGRA_SAIDA = (
    "\n\nFORMATO DA RESPOSTA: escreva texto puro, como mensagem de WhatsApp. "
    "NUNCA use asteriscos, negrito ou markdown. Para listas use traço (-) ou "
    "números. Nunca mostre id ou código técnico — fale das clientes pelo nome. "
    "Valores em reais no padrão brasileiro (R$ 150,00)."
)


def _client() -> Anthropic:
    global _anthropic
    if _anthropic is None:
        _anthropic = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _anthropic


with open("prompts/consultor_prompt.txt", "r", encoding="utf-8") as f:
    CONSULTOR_PROMPT = f.read()


def _montar_contexto_dados() -> str:
    """
    Monta o pacote de dados reais que o consultor usa pra analisar.
    Agora consome as MESMAS ferramentas do agente (Sessão 3) — os números
    nascem do SQL via RPC, idênticos aos que a Tassinha vê no dia a dia.
    Cada bloco é independente: se uma ferramenta falhar, as outras seguem.
    """
    partes = []

    # Faturamento, ticket e volume do mês
    try:
        f = metricas.faturamento()
        partes.append(
            f"Mês atual: {f['qtd_atendimentos']} atendimentos concluídos, "
            f"faturamento R${f['faturamento']:.2f}, ticket médio R${f['ticket_medio']:.2f}."
        )
    except Exception as e:
        logger.warning(f"contexto faturamento falhou: {e}")

    # Saldo do mês (faturamento - despesas)
    try:
        s = financeiro.saldo_mes()
        if "saldo" in s:
            sinal = "positivo" if s["saldo"] >= 0 else "NEGATIVO (no vermelho)"
            partes.append(
                f"Saldo do mês: R${s['saldo']:.2f} ({sinal}) — "
                f"despesas totais R${s['despesas']:.2f}."
            )
    except Exception as e:
        logger.warning(f"contexto saldo falhou: {e}")

    # No-show do mês
    try:
        n = metricas.no_show_rate()
        partes.append(f"No-shows no mês: {n['no_shows']} (taxa {n['taxa_pct']}% sobre {n['base']}).")
    except Exception as e:
        logger.warning(f"contexto noshow falhou: {e}")

    # Taxa de retorno — métrica central
    try:
        t = metricas.taxa_retorno()
        if t.get("clientes_com_atendimento", 0) > 0:
            partes.append(
                f"Taxa de retorno: {t['taxa_pct']}% "
                f"({t['clientes_que_retornaram']} de {t['clientes_com_atendimento']} clientes voltaram 2+ vezes)."
            )
    except Exception as e:
        logger.warning(f"contexto retorno falhou: {e}")

    # Mix de canal
    try:
        m = mix = metricas.mix_canal()
        if m.get("canais"):
            top = ", ".join(f"{c['canal']} {c['pct']}%" for c in m["canais"][:4])
            partes.append(f"Aquisição por canal: {top}.")
    except Exception as e:
        logger.warning(f"contexto mix falhou: {e}")

    # Clientes em risco — com nomes pra o consultor poder ser específico
    try:
        r = risco.clientes_em_risco()
        if r["total"] > 0:
            nomes = ", ".join(
                f"{c['nome']} ({c['dias_desde_ultimo']}d sem voltar, "
                f"cadência {c['cadencia_dias'] or '?'}d, confiança {c['confianca']})"
                for c in r["clientes"][:8]
            )
            partes.append(f"{r['total']} cliente(s) em risco: {nomes}.")
        else:
            partes.append("Nenhuma cliente em risco no momento.")
    except Exception as e:
        logger.warning(f"contexto risco falhou: {e}")

    if not partes:
        return ("ATENÇÃO: não consegui ler nenhum dado do banco. Avise que a "
                "análise está sem base de dados e não invente números.")

    return ("DADOS REAIS DO NEGÓCIO (use só estes números, não invente outros):\n"
            + "\n".join(f"- {p}" for p in partes))


def consultar(pergunta: str) -> str:
    """
    Recebe a pergunta estratégica da Tassinha, monta contexto com dados
    reais, e pede análise ao Sonnet. Retorna o texto da resposta.

    Em caso de falha na API, retorna mensagem de fallback (nunca quebra).
    """
    contexto = _montar_contexto_dados()
    modelo = os.environ.get("MODELO_SONNET", "claude-sonnet-4-6")
    system_completo = f"{CONSULTOR_PROMPT}{_REGRA_SAIDA}\n\nDATA ATUAL: {_data_hoje()}"

    try:
        resp = _client().messages.create(
            model=modelo,
            max_tokens=1024,
            system=system_completo,
            messages=[
                {
                    "role": "user",
                    "content": f"{contexto}\n\nPergunta da Tassinha:\n{pergunta}",
                }
            ],
        )
        # Junta os blocos de texto da resposta
        texto = "".join(
            bloco.text for bloco in resp.content if bloco.type == "text"
        )
        return texto.strip() or "Não consegui formular uma análise agora."
    except Exception as e:
        logger.exception("Falha ao chamar o consultor (Sonnet)")
        return (
            "Consegui levantar os números, mas a análise completa falhou "
            "agora (provavelmente conexão ou limite da API). Tenta de novo "
            "em alguns minutos. Se quiser, posso te passar só os números."
        )
