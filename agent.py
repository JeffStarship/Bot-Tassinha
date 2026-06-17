"""
Camada do agente: recebe texto da Tassinha, decide quais ferramentas
chamar via Gemini, executa, e devolve a resposta em linguagem natural.

Princípio inegociável: a IA NUNCA inventa dado. Todo número/fato vem
do retorno de uma ferramenta. Esta camada só traduz texto <-> chamadas
de função.
"""
import os
import json
import google.generativeai as genai

from tools import clientes, atendimentos, pagamentos, indicacoes

genai.configure(api_key=os.environ["GEMINI_API_KEY"])

with open("prompts/system_prompt.txt", "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

# Mapa nome_da_funcao -> funcao Python real
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
}

# Declaração das ferramentas no formato que o Gemini espera
TOOL_DECLARATIONS = [
    {
        "name": "buscar_cliente",
        "description": "Busca cliente pelo nome (busca parcial). Use sempre antes de registrar atendimento ou criar cliente nova, pra checar se ela já existe.",
        "parameters": {
            "type": "object",
            "properties": {"nome": {"type": "string"}},
            "required": ["nome"],
        },
    },
    {
        "name": "criar_cliente",
        "description": "Cria uma cliente nova no banco.",
        "parameters": {
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
        "parameters": {
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
        "description": "Registra um atendimento realizado (ou agendado). Use depois de confirmar qual cliente_id é o correto.",
        "parameters": {
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
        "parameters": {
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
        "parameters": {
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
        "parameters": {
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
        "parameters": {
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
        "parameters": {
            "type": "object",
            "properties": {"atendimento_id": {"type": "string"}},
            "required": ["atendimento_id"],
        },
    },
    {
        "name": "registrar_indicacao",
        "description": "Registra que uma cliente indicou outra.",
        "parameters": {
            "type": "object",
            "properties": {
                "indicadora_id": {"type": "string"},
                "indicada_id": {"type": "string"},
                "data": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["indicadora_id", "indicada_id", "data"],
        },
    },
]

_model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    system_instruction=SYSTEM_PROMPT,
    tools=[{"function_declarations": TOOL_DECLARATIONS}],
)

# Sessões de chat por usuário (memória de curto prazo por conversa)
_sessions: dict[int, genai.ChatSession] = {}


def _get_session(user_id: int) -> genai.ChatSession:
    if user_id not in _sessions:
        _sessions[user_id] = _model.start_chat()
    return _sessions[user_id]


def reset_session(user_id: int) -> None:
    """Limpa o histórico de conversa de um usuário (evita degradação em contexto longo)."""
    _sessions.pop(user_id, None)


def processar_mensagem(user_id: int, texto: str) -> str:
    """
    Recebe o texto da Tassinha, roda o loop de tool calling até o
    Gemini decidir que tem resposta final, e devolve o texto pra
    enviar de volta no Telegram.
    """
    session = _get_session(user_id)
    response = session.send_message(texto)

    # Loop: enquanto o Gemini pedir chamada de ferramenta, executamos
    # e devolvemos o resultado, até ele responder só com texto.
    max_iteracoes = 8
    for _ in range(max_iteracoes):
        function_calls = [
            part.function_call
            for part in response.candidates[0].content.parts
            if part.function_call
        ]
        if not function_calls:
            break

        tool_responses = []
        for call in function_calls:
            fn_name = call.name
            fn_args = dict(call.args)
            fn = TOOL_REGISTRY.get(fn_name)

            if fn is None:
                result = {"erro": f"ferramenta {fn_name} não existe"}
            else:
                try:
                    result = fn(**fn_args)
                except Exception as e:
                    result = {"erro": str(e)}

            tool_responses.append(
                genai.protos.Part(
                    function_response=genai.protos.FunctionResponse(
                        name=fn_name,
                        response={"result": json.dumps(result, default=str)},
                    )
                )
            )

        response = session.send_message(
            genai.protos.Content(parts=tool_responses)
        )

    return response.text
