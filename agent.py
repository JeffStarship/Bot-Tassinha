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
from tools import metricas, risco, financeiro, servicos, preferencias, produtos
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
    "marcar_cancelamento": atendimentos.marcar_cancelamento,
    "parar_de_avisar_reagendar": atendimentos.parar_de_avisar_reagendar,
    # Sessão 9 — preferências por cliente
    "definir_followup_cliente": preferencias.definir_followup_cliente,
    "resetar_followup_cliente": preferencias.resetar_followup_cliente,
    "ver_followup_cliente": preferencias.ver_followup_cliente,
    # Sessão 11 — produtos / estoque inteligente
    "registrar_compra": produtos.registrar_compra,
    "previsao_produto": produtos.previsao_produto,
    "gasto_produto": produtos.gasto_produto,
    "precos_por_loja": produtos.precos_por_loja,
    "listar_produtos": produtos.listar_produtos,
    "registrar_pagamento": pagamentos.registrar_pagamento,
    "saldo_atendimento": pagamentos.saldo_atendimento,
    "registrar_indicacao": indicacoes.registrar_indicacao,
    "consultar_indicacoes": indicacoes.consultar_indicacoes,
    "listar_indicacoes_ativas": indicacoes.listar_indicacoes_ativas,
    "ranking_indicadoras": indicacoes.ranking_indicadoras,
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
        "description": "Registra que uma cliente indicou outra. A indicada precisa já existir como cliente (use criar_cliente antes se necessário). A conversão e a validade da campanha acontecem automaticamente quando a indicada fizer o primeiro atendimento.",
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
        "name": "consultar_indicacoes",
        "description": "Mostra os números de indicação de uma cliente: total que ela indicou, quantas converteram (viraram cliente) e quantas estão ativas na campanha (dentro da validade). Use pra 'quantas a Bruna indicou', 'quantas indicações da Bruna estão valendo'.",
        "input_schema": {
            "type": "object",
            "properties": {"indicadora_id": {"type": "string"}},
            "required": ["indicadora_id"],
        },
    },
    {
        "name": "listar_indicacoes_ativas",
        "description": "Lista as indicações que estão valendo agora pra uma cliente, com o nome de cada indicada e quantos dias faltam pra expirar. Use pra 'quais indicações da Bruna estão ativas'.",
        "input_schema": {
            "type": "object",
            "properties": {"indicadora_id": {"type": "string"}},
            "required": ["indicadora_id"],
        },
    },
    {
        "name": "ranking_indicadoras",
        "description": "Ranking das clientes que mais indicaram (ordenado por quantas converteram). Use pra 'quem mais me indicou clientes', 'minhas maiores indicadoras'.",
        "input_schema": {
            "type": "object",
            "properties": {"limite": {"type": "integer", "description": "quantas trazer, default 10"}},
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
        "name": "marcar_cancelamento",
        "description": "Marca o próximo atendimento agendado de uma cliente como cancelado. Use quando a Tassia disser 'a Bruna cancelou'. Se ela já vai reagendar em seguida, passe reagendou=True. Se não reagendou, a cliente aparece no resumo diário até reagendar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cliente_id": {"type": "string"},
                "reagendou": {"type": "boolean"},
            },
            "required": ["cliente_id"],
        },
    },
    {
        "name": "parar_de_avisar_reagendar",
        "description": "Para de mostrar uma cliente cancelada na lista de 'falta reagendar' do resumo diário. Use quando a Tassia disser 'pode parar de avisar da Bruna' ou 'não precisa mais lembrar de reagendar a Bruna'.",
        "input_schema": {
            "type": "object",
            "properties": {"cliente_id": {"type": "string"}},
            "required": ["cliente_id"],
        },
    },
    {
        "name": "definir_followup_cliente",
        "description": "Customiza o follow-up de UMA cliente específica (sobrescreve o padrão). Pode mudar: ligar/desligar (ativo), texto do lembrete de dias antes, texto do lembrete de horas antes, quantos dias antes, quantas horas antes. Só passe os campos que vão mudar. SEMPRE confirme com a Tassia o que vai mudar antes de chamar. Nos textos, use {nome} e {hora} que são preenchidos sozinhos.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cliente_id": {"type": "string"},
                "ativo": {"type": "boolean", "description": "False desliga os lembretes dessa cliente"},
                "texto_dias_antes": {"type": "string"},
                "texto_horas_antes": {"type": "string"},
                "dias_antes": {"type": "integer"},
                "horas_antes": {"type": "integer"},
            },
            "required": ["cliente_id"],
        },
    },
    {
        "name": "resetar_followup_cliente",
        "description": "Remove todas as customizações de follow-up de uma cliente — ela volta ao padrão. Use quando a Tassia disser 'volta a Bruna pro padrão'.",
        "input_schema": {
            "type": "object",
            "properties": {"cliente_id": {"type": "string"}},
            "required": ["cliente_id"],
        },
    },
    {
        "name": "ver_followup_cliente",
        "description": "Mostra as customizações de follow-up de uma cliente (ou diz que ela usa o padrão). Use pra 'como está o lembrete da Bruna'.",
        "input_schema": {
            "type": "object",
            "properties": {"cliente_id": {"type": "string"}},
            "required": ["cliente_id"],
        },
    },
    {
        "name": "registrar_compra",
        "description": "Registra a compra de um produto (insumo). Cria o produto se não existir. A Tassia diz o que comprou, quanto pagou, quantos e onde. Ex: 'comprei 3 potes de gel a 50 reais na Loja A'. O sistema aprende sozinho de quanto em quanto tempo/atendimentos o produto acaba, pelas recompras.",
        "input_schema": {
            "type": "object",
            "properties": {
                "produto": {"type": "string"},
                "preco_unitario": {"type": "number", "description": "preço de UMA unidade"},
                "quantidade": {"type": "number", "description": "quantas unidades, default 1"},
                "loja": {"type": "string"},
                "data_compra": {"type": "string", "description": "YYYY-MM-DD, default hoje"},
                "unidade": {"type": "string", "description": "pote, frasco, etc — opcional"},
            },
            "required": ["produto", "preco_unitario"],
        },
    },
    {
        "name": "previsao_produto",
        "description": "Mostra quanto um produto costuma durar (em atendimentos e dias) e quanto já foi consumido desde a última compra. Use pra 'quando o gel vai acabar', 'preciso comprar mais esmalte?'.",
        "input_schema": {
            "type": "object",
            "properties": {"produto": {"type": "string"}},
            "required": ["produto"],
        },
    },
    {
        "name": "gasto_produto",
        "description": "Quanto a Tassia já gastou com um produto no total. Use pra 'quanto já gastei em gel'.",
        "input_schema": {
            "type": "object",
            "properties": {"produto": {"type": "string"}},
            "required": ["produto"],
        },
    },
    {
        "name": "precos_por_loja",
        "description": "Mostra o preço de um produto por loja (onde está mais barato). Use quando a Tassia for comprar: 'onde o gel tá mais barato', 'quanto paguei de esmalte da última vez'.",
        "input_schema": {
            "type": "object",
            "properties": {"produto": {"type": "string"}},
            "required": ["produto"],
        },
    },
    {
        "name": "listar_produtos",
        "description": "Lista todos os produtos cadastrados. Use pra 'quais produtos eu tenho cadastrados'.",
        "input_schema": {"type": "object", "properties": {}},
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


def processar_mensagem(user_id: int, texto: str, forcar_consultor: bool = False,
                       imagem_b64: str = None, imagem_media_type: str = None) -> str:
    """
    Roda o loop de tool calling até o Haiku dar a resposta final.
    Se forcar_consultor=True (comando /consultor), escala direto pro Sonnet.
    Se imagem_b64 vier preenchido, manda a imagem junto pro modelo interpretar
    (o Claude enxerga imagem nativamente — extrai contato, comprovante, cupom,
    print de conversa, etc, e usa as tools normais).
    """
    if forcar_consultor:
        return consultor.consultar(texto)

    history = _get_history(user_id)

    if imagem_b64:
        # mensagem multimodal: imagem + texto no mesmo turno
        conteudo = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": imagem_media_type or "image/jpeg",
                    "data": imagem_b64,
                },
            },
            {"type": "text", "text": texto or "Interprete esta imagem e me ajude com o que for relevante."},
        ]
        history.append({"role": "user", "content": conteudo})
    else:
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
