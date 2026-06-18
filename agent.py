"""
Camada do agente (Haiku 4.5 via Anthropic API).

Recebe texto da Tassinha, decide quais ferramentas chamar, executa, e
devolve a resposta em linguagem natural.

Princípios inegociáveis:
- A IA NUNCA inventa dado. Todo número/fato vem do retorno de uma ferramenta.
- Haiku NUNCA dá conselho de negócio sozinho. Perguntas de análise/conselho
  são escaladas pro consultor (Sonnet) via ferramenta escalar_para_consultor.
"""
import os
import json
import logging
from anthropic import Anthropic

from tools import clientes, atendimentos, pagamentos, indicacoes
import consultor

logger = logging.getLogger(__name__)

_anthropic: Anthropic | None = None


def _client() -> Anthropic:
    global _anthropic
    if _anthropic is None:
        _anthropic = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _anthropic


with open("prompts/system_prompt.txt", "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

MODELO_HAIKU = os.environ.get("MODELO_HAIKU", "claude-haiku-4-5")

# Mapa nome -> função Python real
TOOL_REGISTRY = {
    "buscar_cliente": clientes.buscar_cliente,
    "criar_cliente": clientes.criar_cliente,
    "atualizar_cliente": clientes.atualizar_cliente,
    "registrar_atendimento": atendimentos.registrar_atendimento,
    "agendar": atendimentos.agendar,
    "listar_agenda": atendimentos.listar_agenda,
    "atualizar_status_atendimento": atendimentos.atualizar_status_atendimento,
    "registrar_pagamento": pagamentos.registrar_pagamento,
    "saldo_atendimento": pagamentos.saldo_atendimento,
    "registrar_indicacao": indicacoes.registrar_indicacao,
    "escalar_para_consultor": lambda pergunta: {"resposta_consultor": consultor.consultar(pergunta)},
}

# Declaração das ferramentas no formato da API Anthropic
TOOLS = [
    {
        "name": "buscar_cliente",
        "description": "Busca cliente pelo nome (busca parcial). Use sempre antes de registrar atendimento ou criar cliente nova, pra checar se ela já existe.",
        "input_schema": {
            "type": "object",
            "properties": {"nome": {"type": "string"}},
            "required": ["nome"],
        },
    },
    {
        "name": "criar_cliente",
        "description": "Cria uma cliente nova no banco.",
        "input_schema": {
            "type": "object",
            "properties": {
                "nome": {"type": "string"},
                "telefone": {"type": "string"},
                "instagram": {"type": "string"},
                "canal_aquisicao": {
                    "type": "string",
                    "enum": ["marketplace", "indicacao", "instagram", "boost", "outro"],
                },
                "observacoes": {"type": "string"},
            },
            "required": ["nome"],
        },
    },
    {
        "name": "atualizar_cliente",
        "description": "Atualiza dados de uma cliente já existente.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cliente_id": {"type": "string"},
                "campos": {"type": "object"},
            },
            "required": ["cliente_id", "campos"],
        },
    },
    {
        "name": "registrar_atendimento",
        "description": "Registra um atendimento realizado. Use depois de confirmar qual cliente_id é o correto.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cliente_id": {"type": "string"},
                "data": {"type": "string", "description": "formato YYYY-MM-DD"},
                "servico": {"type": "string"},
                "valor": {"type": "number"},
                "duracao_min": {"type": "integer"},
                "design": {"type": "string"},
                "notas": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["agendado", "concluido", "no_show", "cancelado"],
                },
            },
            "required": ["cliente_id", "data", "servico", "valor"],
        },
    },
    {
        "name": "agendar",
        "description": "Agenda um atendimento futuro, sem valor definido ainda.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cliente_id": {"type": "string"},
                "data": {"type": "string", "description": "formato YYYY-MM-DD"},
                "servico": {"type": "string"},
            },
            "required": ["cliente_id", "data"],
        },
    },
    {
        "name": "listar_agenda",
        "description": "Lista agendamentos futuros num período.",
        "input_schema": {
            "type": "object",
            "properties": {
                "data_inicio": {"type": "string", "description": "YYYY-MM-DD"},
                "data_fim": {"type": "string", "description": "YYYY-MM-DD, opcional"},
            },
            "required": ["data_inicio"],
        },
    },
    {
        "name": "atualizar_status_atendimento",
        "description": "Marca um atendimento como concluído, no-show ou cancelado.",
        "input_schema": {
            "type": "object",
            "properties": {
                "atendimento_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": ["agendado", "concluido", "no_show", "cancelado"],
                },
            },
            "required": ["atendimento_id", "status"],
        },
    },
    {
        "name": "registrar_pagamento",
        "description": "Registra um pagamento (sinal, restante ou total) de um atendimento.",
        "input_schema": {
            "type": "object",
            "properties": {
                "atendimento_id": {"type": "string"},
                "tipo": {"type": "string", "enum": ["sinal", "restante", "total"]},
                "valor": {"type": "number"},
                "forma": {"type": "string", "enum": ["pix", "dinheiro", "cartao"]},
                "data": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["atendimento_id", "tipo", "valor", "forma", "data"],
        },
    },
    {
        "name": "saldo_atendimento",
        "description": "Mostra quanto já foi pago e quanto falta de um atendimento.",
        "input_schema": {
            "type": "object",
            "properties": {"atendimento_id": {"type": "string"}},
            "required": ["atendimento_id"],
        },
    },
    {
        "name": "registrar_indicacao",
        "description": "Registra que uma cliente indicou outra.",
        "input_schema": {
            "type": "object",
            "properties": {
                "indicadora_id": {"type": "string"},
                "indicada_id": {"type": "string"},
                "data": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["indicadora_id", "indicada_id", "data"],
        },
    },
    {
        "name": "escalar_para_consultor",
        "description": (
            "Escala a pergunta para o consultor estratégico (modelo avançado que "
            "analisa os dados reais do negócio). Use SEMPRE que a Tassinha pedir "
            "análise, opinião, julgamento, explicação de causa, comparação "
            "interpretada, ou conselho sobre o negócio — qualquer coisa que não "
            "seja um registro ou uma consulta direta de número/lista. Na dúvida "
            "entre responder você mesmo e escalar, SEMPRE escale. Você NUNCA dá "
            "conselho de negócio por conta própria."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pergunta": {
                    "type": "string",
                    "description": "A pergunta da Tassinha, repassada na íntegra ou levemente reformulada para clareza.",
                }
            },
            "required": ["pergunta"],
        },
    },
]

# Histórico de conversa por usuário (memória de curto prazo)
_sessions: dict[int, list] = {}


def _get_history(user_id: int) -> list:
    return _sessions.setdefault(user_id, [])


def reset_session(user_id: int) -> None:
    _sessions.pop(user_id, None)


def processar_mensagem(user_id: int, texto: str, forcar_consultor: bool = False) -> str:
    """
    Roda o loop de tool calling até o Haiku dar a resposta final.
    Se forcar_consultor=True (comando /consultor), escala direto pro Sonnet.
    """
    if forcar_consultor:
        return consultor.consultar(texto)

    history = _get_history(user_id)
    history.append({"role": "user", "content": texto})

    max_iteracoes = 8
    for _ in range(max_iteracoes):
        resp = _client().messages.create(
            model=MODELO_HAIKU,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=history,
        )

        # Adiciona a resposta do assistant ao histórico
        history.append({"role": "assistant", "content": resp.content})

        # Verifica se há tool_use nos blocos
        tool_uses = [b for b in resp.content if b.type == "tool_use"]

        if not tool_uses:
            # Resposta final: junta os blocos de texto
            texto_final = "".join(
                b.text for b in resp.content if b.type == "text"
            )
            return texto_final.strip() or "Ok."

        # Executa cada ferramenta e monta os tool_results
        tool_results = []
        for tu in tool_uses:
            fn = TOOL_REGISTRY.get(tu.name)
            if fn is None:
                resultado = {"erro": f"ferramenta {tu.name} não existe"}
            else:
                try:
                    resultado = fn(**tu.input)
                except Exception as e:
                    logger.exception(
                        f"Falha na ferramenta '{tu.name}' com input {tu.input}"
                    )
                    resultado = {"erro": str(e)}

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": json.dumps(resultado, default=str, ensure_ascii=False),
            })

        # Devolve os resultados como mensagem do usuário
        history.append({"role": "user", "content": tool_results})

    return "Precisei de muitos passos pra isso e parei por segurança. Tenta reformular a mensagem."
