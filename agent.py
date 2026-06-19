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
from datetime import datetime
import pytz
from anthropic import Anthropic

from tools import clientes, atendimentos, pagamentos, indicacoes
from tools import metricas, risco, financeiro, servicos
import consultor

logger = logging.getLogger(__name__)

_anthropic: Anthropic | None = None

# Dias da semana em português para a data por extenso
_DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
         "sexta-feira", "sábado", "domingo"]
_MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
          "agosto", "setembro", "outubro", "novembro", "dezembro"]


def _data_hoje() -> str:
    """
    Data de hoje no timezone do projeto, por extenso em português.
    Injetada no system prompt a cada mensagem — o modelo não tem relógio
    próprio, então sem isso ele chuta o ano (foi o que causou o bug de 2024).
    """
    tz = pytz.timezone(os.environ.get("TIMEZONE", "America/Sao_Paulo"))
    agora = datetime.now(tz)
    dia_semana = _DIAS[agora.weekday()]
    return (f"Hoje é {dia_semana}, {agora.day} de {_MESES[agora.month - 1]} "
            f"de {agora.year} ({agora.strftime('%d/%m/%Y')}), "
            f"{agora.strftime('%H:%M')} no horário de São Paulo. "
            f"Sempre que a pessoa disser 'hoje', 'ontem', 'esse mês', use esta "
            f"data como referência. A data no formato ISO para registros é "
            f"{agora.strftime('%Y-%m-%d')}.")


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
    "ajustar_inicio_atendimento": atendimentos.ajustar_inicio_atendimento,
    "registrar_pagamento": pagamentos.registrar_pagamento,
    "saldo_atendimento": pagamentos.saldo_atendimento,
    "registrar_indicacao": indicacoes.registrar_indicacao,
    # Sessão 5 — serviços/preços
    "cadastrar_servico": servicos.cadastrar_servico,
    "listar_servicos": servicos.listar_servicos,
    "buscar_preco_servico": servicos.buscar_preco_servico,
    # Sessão 3 — métricas
    "faturamento": metricas.faturamento,
    "no_show_rate": metricas.no_show_rate,
    "mix_canal": metricas.mix_canal,
    "taxa_retorno": metricas.taxa_retorno,
    # Sessão 3 — risco e cadência
    "clientes_em_risco": risco.clientes_em_risco,
    "cadencia_cliente": risco.cadencia_cliente,
    "ranking_inatividade": risco.ranking_inatividade,
    # Sessão 3 — financeiro
    "registrar_despesa": financeiro.registrar_despesa,
    "despesas_recorrentes": financeiro.despesas_recorrentes,
    "despesas_do_mes": financeiro.despesas_do_mes,
    "saldo_mes": financeiro.saldo_mes,
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
        "description": "Registra um atendimento realizado. Use depois de confirmar qual cliente_id é o correto. Se a Tassia disser só o nome do serviço sem o valor, use buscar_preco_servico antes pra puxar o preço do catálogo.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cliente_id": {"type": "string"},
                "data": {"type": "string", "description": "formato YYYY-MM-DD"},
                "hora": {"type": "string", "description": "formato HH:MM, opcional"},
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
        "description": "Agenda um atendimento futuro. Sempre tente capturar a HORA — os lembretes de início/fim e os follow-ups dependem dela. Se o serviço estiver no catálogo, use buscar_preco_servico pra preencher valor e duração.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cliente_id": {"type": "string"},
                "data": {"type": "string", "description": "formato YYYY-MM-DD"},
                "hora": {"type": "string", "description": "formato HH:MM — importante pros lembretes"},
                "servico": {"type": "string"},
                "valor": {"type": "number", "description": "puxe do catálogo se possível"},
                "duracao_min": {"type": "integer"},
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
        "name": "ajustar_inicio_atendimento",
        "description": "Ajusta o início real de um atendimento de hoje pros lembretes de início/fim caírem certo quando há atraso. Use quando a Tassia disser que uma cliente ainda não começou ('a Bruna ainda não começou' -> ainda_nao_comecou=True) ou a que horas começou ('começou agora' / 'começou às 15h' -> hora_inicio). Identifique pela cliente (cliente_id de buscar_cliente).",
        "input_schema": {
            "type": "object",
            "properties": {
                "cliente_id": {"type": "string"},
                "ainda_nao_comecou": {"type": "boolean"},
                "hora_inicio": {"type": "string", "description": "HH:MM, quando começou de fato"},
            },
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
        "name": "faturamento",
        "description": "Faturamento, número de atendimentos concluídos e ticket médio de um mês. Use pra perguntas tipo 'quanto faturei esse mês', 'qual meu ticket médio'. Sem mês informado, usa o mês atual.",
        "input_schema": {
            "type": "object",
            "properties": {
                "mes_referencia": {"type": "string", "description": "mês no formato AAAA-MM. Opcional — default é o mês atual."}
            },
        },
    },
    {
        "name": "no_show_rate",
        "description": "Taxa de no-show (faltas) de um mês, em %. Base = atendimentos concluídos + no-shows. Use pra 'quantas faltas tive', 'minha taxa de no-show'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "mes_referencia": {"type": "string", "description": "AAAA-MM. Opcional — default mês atual."}
            },
        },
    },
    {
        "name": "mix_canal",
        "description": "Distribuição da base de clientes por canal de aquisição (indicação, instagram, marketplace, etc) com quantidade e %. Use pra 'de onde vêm minhas clientes', 'qual canal traz mais gente'.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "taxa_retorno",
        "description": "Taxa de retorno: % de clientes que voltaram (2+ atendimentos) sobre as que vieram ao menos 1 vez. Métrica central do negócio recorrente. Use pra 'minhas clientes estão voltando', 'taxa de retenção'.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "clientes_em_risco",
        "description": "Lista clientes que estão passando do tempo normal de voltar (em risco de sumir), da mais atrasada pra menos. Considera a cadência individual de cada uma. Use pra 'quem está sumindo', 'quem eu preciso chamar de volta', 'clientes em risco'.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "cadencia_cliente",
        "description": "De quantos em quantos dias UMA cliente específica costuma voltar, com nível de confiança (fraca/media/solida) pela quantidade de visitas. Use o cliente_id de buscar_cliente. Use pra 'de quanto em quanto tempo a Fulana volta'.",
        "input_schema": {
            "type": "object",
            "properties": {"cliente_id": {"type": "string"}},
            "required": ["cliente_id"],
        },
    },
    {
        "name": "ranking_inatividade",
        "description": "Lista as clientes pela inatividade (há mais tempo sem voltar primeiro). Foto geral pra decidir quem reativar. Use pra 'quem está há mais tempo sem aparecer', 'lista de inativas'.",
        "input_schema": {
            "type": "object",
            "properties": {"limite": {"type": "integer", "description": "quantas trazer, default 20"}},
        },
    },
    {
        "name": "registrar_despesa",
        "description": "Registra uma despesa do negócio. Use recorrente=true pra custo fixo mensal (aluguel, etc). Use pra 'paguei X de material', 'lança aluguel de 500 todo mês'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "descricao": {"type": "string"},
                "valor": {"type": "number"},
                "categoria": {"type": "string", "enum": ["aluguel", "material", "marketing", "transporte", "contas", "outros"]},
                "data": {"type": "string", "description": "AAAA-MM-DD, default hoje"},
                "recorrente": {"type": "boolean"},
                "dia_recorrencia": {"type": "integer", "description": "dia do mês que repete, só pra recorrente"},
            },
            "required": ["descricao", "valor"],
        },
    },
    {
        "name": "despesas_recorrentes",
        "description": "Lista as despesas fixas mensais ativas e o total. Use pra 'quais meus custos fixos', 'quanto gasto fixo por mês'.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "despesas_do_mes",
        "description": "Total de despesas de um mês, separando pontuais de recorrentes. Use pra 'quanto gastei esse mês'.",
        "input_schema": {
            "type": "object",
            "properties": {"mes_referencia": {"type": "string", "description": "AAAA-MM, default mês atual"}},
        },
    },
    {
        "name": "saldo_mes",
        "description": "Saldo do mês: faturamento menos despesas. Saldo negativo = mês no vermelho. Use pra 'fechei no azul esse mês', 'qual meu lucro', 'sobrou quanto'.",
        "input_schema": {
            "type": "object",
            "properties": {"mes_referencia": {"type": "string", "description": "AAAA-MM, default mês atual"}},
        },
    },
    {
        "name": "cadastrar_servico",
        "description": "Cadastra ou atualiza UM serviço no catálogo de preços. Quando a Tassia mandar vários serviços de uma vez (ex: 'alongamento 150, manutenção 90, banho de gel 70'), chame esta ferramenta uma vez para cada serviço. Se o serviço já existe, o preço é atualizado.",
        "input_schema": {
            "type": "object",
            "properties": {
                "nome": {"type": "string"},
                "preco": {"type": "number"},
                "duracao_min": {"type": "integer", "description": "opcional, default 90"},
            },
            "required": ["nome", "preco"],
        },
    },
    {
        "name": "listar_servicos",
        "description": "Lista todos os serviços do catálogo com preço e duração. Use quando a Tassia pedir pra ver a tabela de preços.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "buscar_preco_servico",
        "description": "Busca o preço de um serviço pelo nome. Use ao registrar atendimento quando a Tassia disser só o serviço sem o valor. Se achar 1, use o preço. Se achar vários, pergunte qual. Se não achar, ofereça cadastrar perguntando o preço.",
        "input_schema": {
            "type": "object",
            "properties": {"nome": {"type": "string"}},
            "required": ["nome"],
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

    system_com_data = f"{SYSTEM_PROMPT}\n\nDATA ATUAL:\n{_data_hoje()}"

    max_iteracoes = 8
    for _ in range(max_iteracoes):
        resp = _client().messages.create(
            model=MODELO_HAIKU,
            max_tokens=1024,
            system=system_com_data,
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
