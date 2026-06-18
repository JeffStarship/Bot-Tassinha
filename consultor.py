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
from datetime import date, timedelta
from anthropic import Anthropic
from database import get_client

logger = logging.getLogger(__name__)

_anthropic: Anthropic | None = None


def _client() -> Anthropic:
    global _anthropic
    if _anthropic is None:
        _anthropic = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _anthropic


with open("prompts/consultor_prompt.txt", "r", encoding="utf-8") as f:
    CONSULTOR_PROMPT = f.read()


def _montar_contexto_dados() -> str:
    """
    Lê dados reais do banco pra dar contexto ao consultor.
    Robusto: cada bloco é independente — se um falhar, os outros seguem.
    Quando a Sessão 3 adicionar tools de métrica mais ricas, dá pra
    substituir essas queries diretas pelas ferramentas.
    """
    db = get_client()
    hoje = date.today()
    inicio_mes = hoje.replace(day=1)
    partes = []

    # Faturamento e atendimentos do mês atual
    try:
        atend = (
            db.table("atendimentos")
            .select("valor, status, data, cliente_id")
            .gte("data", inicio_mes.isoformat())
            .eq("status", "concluido")
            .execute()
        )
        total = sum(float(a["valor"]) for a in atend.data)
        qtd = len(atend.data)
        ticket = round(total / qtd, 2) if qtd else 0
        partes.append(
            f"Mês atual ({inicio_mes.strftime('%m/%Y')}): "
            f"{qtd} atendimentos concluídos, faturamento R${total:.2f}, "
            f"ticket médio R${ticket:.2f}."
        )
    except Exception as e:
        logger.warning(f"contexto faturamento falhou: {e}")
        partes.append("Não consegui ler o faturamento do mês.")

    # No-shows do mês
    try:
        noshow = (
            db.table("atendimentos")
            .select("id", count="exact")
            .gte("data", inicio_mes.isoformat())
            .eq("status", "no_show")
            .execute()
        )
        partes.append(f"No-shows no mês: {noshow.count or 0}.")
    except Exception as e:
        logger.warning(f"contexto noshow falhou: {e}")

    # Total de clientes e status
    try:
        clientes = db.table("clientes").select("status").execute()
        total_cli = len(clientes.data)
        ativas = sum(1 for c in clientes.data if c["status"] == "ativa")
        partes.append(f"Base de clientes: {total_cli} no total, {ativas} ativas.")
    except Exception as e:
        logger.warning(f"contexto clientes falhou: {e}")

    # Clientes sem voltar há mais de 30 dias (proxy simples de risco até a Sessão 3)
    try:
        limite = (hoje - timedelta(days=30)).isoformat()
        # último atendimento por cliente — leitura simples
        todos = (
            db.table("atendimentos")
            .select("cliente_id, data")
            .eq("status", "concluido")
            .order("data", desc=True)
            .execute()
        )
        ultimo_por_cliente = {}
        for a in todos.data:
            cid = a["cliente_id"]
            if cid not in ultimo_por_cliente:
                ultimo_por_cliente[cid] = a["data"]
        em_risco = sum(1 for d in ultimo_por_cliente.values() if d < limite)
        partes.append(
            f"Clientes que não voltam há mais de 30 dias: {em_risco}."
        )
    except Exception as e:
        logger.warning(f"contexto risco falhou: {e}")

    if not partes:
        return "ATENÇÃO: não consegui ler nenhum dado do banco. Avise que a análise está sem base de dados e não invente números."

    return "DADOS REAIS DO NEGÓCIO (use só estes números, não invente outros):\n" + "\n".join(f"- {p}" for p in partes)


def consultar(pergunta: str) -> str:
    """
    Recebe a pergunta estratégica da Tassinha, monta contexto com dados
    reais, e pede análise ao Sonnet. Retorna o texto da resposta.

    Em caso de falha na API, retorna mensagem de fallback (nunca quebra).
    """
    contexto = _montar_contexto_dados()
    modelo = os.environ.get("MODELO_SONNET", "claude-sonnet-4-6")

    try:
        resp = _client().messages.create(
            model=modelo,
            max_tokens=1024,
            system=CONSULTOR_PROMPT,
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
