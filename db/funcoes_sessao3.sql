-- ============================================================================
-- Bot Tassinha — Funções SQL (RPC) da Sessão 3
-- ============================================================================
-- Estas funções fazem a AGREGAÇÃO dentro do Postgres. As tools Python só
-- chamam via db.rpc(...) e transportam o número pronto. Isso garante:
--   1. Todo número nasce de SQL (regra inegociável do projeto).
--   2. Sem limite de 1000 linhas do PostgREST — a soma é da tabela inteira.
--   3. Cálculo atômico sobre estado consistente do banco.
--
-- COMO APLICAR: cole este arquivo inteiro no SQL Editor do Supabase e rode.
-- É idempotente (CREATE OR REPLACE) — pode rodar de novo sem quebrar nada.
--
-- Convenção de período: todas as funções de período recebem p_inicio e p_fim
-- como DATE (YYYY-MM-DD). Passar o mês inteiro = primeiro e último dia do mês.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- MÉTRICAS FINANCEIRAS E OPERACIONAIS
-- ----------------------------------------------------------------------------

-- Faturamento, qtd de atendimentos e ticket médio num período.
-- Considera apenas atendimentos concluídos.
create or replace function metrica_faturamento(p_inicio date, p_fim date)
returns table (
    faturamento numeric,
    qtd_atendimentos bigint,
    ticket_medio numeric
)
language sql
stable
as $$
    select
        coalesce(sum(valor), 0)::numeric                                as faturamento,
        count(*)::bigint                                                as qtd_atendimentos,
        coalesce(round(avg(valor), 2), 0)::numeric                      as ticket_medio
    from atendimentos
    where status = 'concluido'
      and data >= p_inicio
      and data <= p_fim;
$$;


-- No-show rate num período.
-- Base = concluídos + no-shows (atendimentos que "deveriam acontecer").
-- Agendados futuros e cancelados não entram na base.
create or replace function metrica_no_show(p_inicio date, p_fim date)
returns table (
    no_shows bigint,
    base bigint,
    taxa_pct numeric
)
language sql
stable
as $$
    with j as (
        select
            count(*) filter (where status = 'no_show')                     as no_shows,
            count(*) filter (where status in ('concluido', 'no_show'))      as base
        from atendimentos
        where data >= p_inicio
          and data <= p_fim
    )
    select
        no_shows,
        base,
        case when base > 0
             then round(no_shows::numeric * 100 / base, 1)
             else 0 end as taxa_pct
    from j;
$$;


-- Mix de aquisição: quantas clientes por canal, e participação %.
-- Conta sobre toda a base de clientes (não por período — é estrutural).
create or replace function metrica_mix_canal()
returns table (
    canal text,
    qtd bigint,
    pct numeric
)
language sql
stable
as $$
    with total as (select count(*)::numeric as t from clientes)
    select
        coalesce(canal_aquisicao, 'nao_informado') as canal,
        count(*)::bigint                           as qtd,
        case when (select t from total) > 0
             then round(count(*)::numeric * 100 / (select t from total), 1)
             else 0 end                            as pct
    from clientes
    group by coalesce(canal_aquisicao, 'nao_informado')
    order by qtd desc;
$$;


-- Taxa de retorno: % de clientes que voltaram (2+ atendimentos concluídos)
-- sobre as clientes que tiveram ao menos 1 atendimento concluído.
-- Esta é a métrica-mãe do negócio dela (serviço recorrente).
create or replace function metrica_taxa_retorno()
returns table (
    clientes_com_atendimento bigint,
    clientes_que_retornaram bigint,
    taxa_pct numeric
)
language sql
stable
as $$
    with por_cliente as (
        select cliente_id, count(*) as n
        from atendimentos
        where status = 'concluido'
        group by cliente_id
    )
    select
        count(*)::bigint                                   as clientes_com_atendimento,
        count(*) filter (where n >= 2)::bigint             as clientes_que_retornaram,
        case when count(*) > 0
             then round(count(*) filter (where n >= 2)::numeric * 100 / count(*), 1)
             else 0 end                                    as taxa_pct
    from por_cliente;
$$;


-- ----------------------------------------------------------------------------
-- CADÊNCIA E RISCO
-- ----------------------------------------------------------------------------

-- Cadência individual de UMA cliente: intervalo médio (dias) entre
-- atendimentos concluídos consecutivos, + nível de confiança pela contagem.
-- Confiança: <3 visitas = fraca, 3-5 = media, 6+ = solida.
-- Com menos de 2 visitas não há intervalo calculável -> cadencia_dias NULL.
create or replace function cadencia_cliente(p_cliente_id uuid)
returns table (
    cliente_id uuid,
    visitas bigint,
    cadencia_dias numeric,
    confianca text,
    ultimo_atendimento date,
    dias_desde_ultimo integer
)
language sql
stable
as $$
    with visitas_cli as (
        select data,
               lag(data) over (order by data) as data_anterior
        from atendimentos
        where cliente_id = p_cliente_id
          and status = 'concluido'
    ),
    intervalos as (
        select (data - data_anterior) as gap
        from visitas_cli
        where data_anterior is not null
    ),
    agg as (
        select
            (select count(*) from visitas_cli)                          as visitas,
            (select round(avg(gap), 1) from intervalos)                 as cadencia_dias,
            (select max(data) from visitas_cli)                         as ultimo
    )
    select
        p_cliente_id,
        visitas,
        cadencia_dias,
        case
            when visitas >= 6 then 'solida'
            when visitas >= 3 then 'media'
            else 'fraca'
        end                                                             as confianca,
        ultimo                                                          as ultimo_atendimento,
        case when ultimo is not null
             then (current_date - ultimo)
             else null end                                             as dias_desde_ultimo
    from agg;
$$;


-- Clientes em risco: passaram do tempo esperado de retorno.
-- Para cada cliente com histórico, calcula cadência individual e marca em
-- risco se dias_desde_ultimo > cadencia * p_multiplicador.
-- Quem tem <2 visitas (sem cadência) usa o fallback p_dias_padrao.
-- Retorna ordenado por "atraso" (quão acima do esperado está) — mais
-- urgente primeiro.
create or replace function clientes_em_risco(
    p_multiplicador numeric default 1.3,
    p_dias_padrao integer default 21
)
returns table (
    cliente_id uuid,
    nome text,
    telefone text,
    visitas bigint,
    cadencia_dias numeric,
    confianca text,
    ultimo_atendimento date,
    dias_desde_ultimo integer,
    limite_dias numeric,
    atraso_dias numeric
)
language sql
stable
as $$
    with gaps as (
        -- para cada atendimento concluído, o intervalo até o anterior da mesma cliente
        select
            cliente_id,
            data,
            (data - lag(data) over (partition by cliente_id order by data)) as diff
        from atendimentos
        where status = 'concluido'
    ),
    base as (
        select
            cliente_id,
            count(*)                       as visitas,
            avg(diff)                      as cadencia_dias,  -- ignora NULL (1ª visita) automaticamente
            max(data)                      as ultimo
        from gaps
        group by cliente_id
    ),
    calc as (
        select
            b.cliente_id,
            b.visitas,
            round(b.cadencia_dias, 1)                                   as cadencia_dias,
            case
                when b.visitas >= 6 then 'solida'
                when b.visitas >= 3 then 'media'
                else 'fraca'
            end                                                        as confianca,
            b.ultimo                                                   as ultimo_atendimento,
            (current_date - b.ultimo)                                  as dias_desde_ultimo,
            -- limite: se tem cadência, cadencia*mult; senão fallback fixo
            case
                when b.cadencia_dias is not null
                    then round(b.cadencia_dias * p_multiplicador, 1)
                else p_dias_padrao::numeric
            end                                                        as limite_dias
        from base b
    )
    select
        c.cliente_id,
        cl.nome,
        cl.telefone,
        c.visitas,
        c.cadencia_dias,
        c.confianca,
        c.ultimo_atendimento,
        c.dias_desde_ultimo,
        c.limite_dias,
        round(c.dias_desde_ultimo - c.limite_dias, 1)                  as atraso_dias
    from calc c
    join clientes cl on cl.id = c.cliente_id
    where c.dias_desde_ultimo > c.limite_dias
      and cl.status <> 'inativa'
    order by (c.dias_desde_ultimo - c.limite_dias) desc;
$$;


-- Ranking de inatividade: todas as clientes ordenadas por dias desde o
-- último atendimento concluído (maior primeiro). Não filtra por risco —
-- é a foto completa pra ela decidir quem reativar.
create or replace function ranking_inatividade(p_limite integer default 20)
returns table (
    cliente_id uuid,
    nome text,
    telefone text,
    ultimo_atendimento date,
    dias_desde_ultimo integer,
    total_visitas bigint
)
language sql
stable
as $$
    select
        cl.id                                        as cliente_id,
        cl.nome,
        cl.telefone,
        max(a.data)                                  as ultimo_atendimento,
        (current_date - max(a.data))                 as dias_desde_ultimo,
        count(*)::bigint                             as total_visitas
    from clientes cl
    join atendimentos a on a.cliente_id = cl.id and a.status = 'concluido'
    group by cl.id, cl.nome, cl.telefone
    order by max(a.data) asc
    limit p_limite;
$$;


-- ----------------------------------------------------------------------------
-- FINANCEIRO — DESPESAS
-- ----------------------------------------------------------------------------

-- Total de despesas num período. Soma despesas pontuais com data no período
-- MAIS as recorrentes ativas (uma incidência por mês do período).
-- Para simplificar e ficar previsível: conta cada recorrente ativa 1x por
-- mês civil tocado pelo período. Para saldo do mês corrente isso é exato.
create or replace function despesas_periodo(p_inicio date, p_fim date)
returns table (
    despesas_pontuais numeric,
    despesas_recorrentes numeric,
    total numeric
)
language sql
stable
as $$
    with pontuais as (
        select coalesce(sum(valor), 0)::numeric as v
        from despesas
        where recorrente = false
          and data >= p_inicio
          and data <= p_fim
    ),
    -- nº de meses civis tocados pelo período (>=1)
    meses as (
        select greatest(
            1,
            (date_part('year', p_fim) - date_part('year', p_inicio)) * 12
            + (date_part('month', p_fim) - date_part('month', p_inicio)) + 1
        )::int as n
    ),
    recorrentes as (
        select coalesce(sum(valor), 0)::numeric * (select n from meses) as v
        from despesas
        where recorrente = true
          and ativa = true
    )
    select
        (select v from pontuais)                                   as despesas_pontuais,
        (select v from recorrentes)                                as despesas_recorrentes,
        (select v from pontuais) + (select v from recorrentes)     as total;
$$;


-- Saldo do mês: faturamento concluído - despesas (pontuais + recorrentes)
-- do mês indicado por uma data qualquer dentro dele.
create or replace function saldo_mes(p_data_no_mes date)
returns table (
    inicio date,
    fim date,
    faturamento numeric,
    despesas numeric,
    saldo numeric
)
language sql
stable
as $$
    with bounds as (
        select
            date_trunc('month', p_data_no_mes)::date                       as ini,
            (date_trunc('month', p_data_no_mes) + interval '1 month - 1 day')::date as f
    ),
    fat as (
        select coalesce(sum(valor), 0)::numeric as v
        from atendimentos, bounds
        where status = 'concluido'
          and data >= bounds.ini
          and data <= bounds.f
    ),
    desp as (
        select total as v
        from bounds, despesas_periodo(bounds.ini, bounds.f)
    )
    select
        bounds.ini,
        bounds.f,
        (select v from fat)                          as faturamento,
        (select v from desp)                         as despesas,
        (select v from fat) - (select v from desp)   as saldo
    from bounds;
$$;
