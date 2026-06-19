-- ============================================================================
-- Bot Tassinha — Funções SQL (RPC) da Sessão 4 (scheduler)
-- ============================================================================
-- Idempotente (CREATE OR REPLACE). Cole no SQL Editor do Supabase e rode.
-- ============================================================================


-- Conta atendimentos concluídos num período e soma o faturamento.
-- Usada pelas comparações semana-vs-semana e mês-vs-mês do scheduler.
-- (metrica_faturamento já existe e faz isso, mas esta retorna só o par
--  enxuto qtd+valor que o digest precisa, sem ticket médio.)
create or replace function atendimentos_periodo(p_inicio date, p_fim date)
returns table (
    qtd bigint,
    faturamento numeric
)
language sql
stable
as $$
    select
        count(*)::bigint                        as qtd,
        coalesce(sum(valor), 0)::numeric        as faturamento
    from atendimentos
    where status = 'concluido'
      and data >= p_inicio
      and data <= p_fim;
$$;


-- Aniversariantes num intervalo de dias a partir de hoje.
-- p_dias_a_frente = 0 -> só hoje; 1 -> hoje e amanhã.
-- Compara mês/dia (ignora o ano de nascimento). Lida com a virada de ano
-- (ex: hoje 31/12, amanhã 01/01) montando a lista de (mês,dia) alvo.
create or replace function aniversariantes(p_dias_a_frente integer default 1)
returns table (
    cliente_id uuid,
    nome text,
    telefone text,
    data_nascimento date,
    dia_aniversario date,
    quando text
)
language sql
stable
as $$
    with alvos as (
        select
            (current_date + g)::date                              as alvo,
            g                                                     as offset_dias
        from generate_series(0, p_dias_a_frente) g
    )
    select
        cl.id                                                     as cliente_id,
        cl.nome,
        cl.telefone,
        cl.data_nascimento,
        a.alvo                                                    as dia_aniversario,
        case when a.offset_dias = 0 then 'hoje' else 'amanhã' end as quando
    from clientes cl
    join alvos a
      on extract(month from cl.data_nascimento) = extract(month from a.alvo)
     and extract(day   from cl.data_nascimento) = extract(day   from a.alvo)
    where cl.data_nascimento is not null
    order by a.offset_dias, cl.nome;
$$;
