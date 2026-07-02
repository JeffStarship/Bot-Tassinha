-- ============================================================================
-- Bot Tassinha — SQL da Sessão 15 (sinal / entrada de pagamento)
-- ============================================================================
-- Idempotente. Cole no SQL Editor do Supabase e rode.
--
-- MODELO (decidido com o Paulo):
--   - Sinal = pagamento parcial antecipado, registrado em `pagamentos` com
--     tipo='sinal', ligado ao atendimento. Pode haver VÁRIOS sinais por
--     atendimento (somam). Todos antes da data do atendimento.
--   - Estado de pagamento (derivado, não armazenado):
--       sem_sinal | parcial (pago X, falta Y) | integral (pago tudo)
--     Fonte única de verdade = soma dos pagamentos do atendimento.
--   - Sinal NUNCA >= valor total (é só confirmação). Se registrarem >=, o
--     código Python alerta a Tassia (não bloqueia no banco).
--   - Faturamento: total conta no DIA DO ATENDIMENTO (já é o comportamento;
--     o sinal está dentro de atendimentos.valor). Sinal RETIDO por perda
--     (no-show/cancelou e perdeu) entra como receita no dia da retenção,
--     SEM duplicar (atendimento perdido não é 'concluido').
--   - Remarcar preserva o sinal (mesma reserva muda de dia) -> nada a fazer
--     no banco além de manter o atendimento; os pagamentos seguem ligados.
--   - Cancelar: a Tassia decide o destino do sinal — vira CRÉDITO da cliente
--     (tabela creditos_cliente) ou é retido (perdido, vira receita).
--   - Crédito é aplicado num atendimento futuro SÓ com confirmação da Tassia.
--   - Sem regra de tempo (6h/2 dias): a palavra da Tassia decide sempre.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1. Coluna de controle em pagamentos: marca sinal retido (perdido) como
--    receita reconhecida, com a data da retenção.
-- ----------------------------------------------------------------------------
-- tipo já aceita 'sinal','restante','total'. Adiciona 'sinal_retido' pra
-- registrar a receita de um sinal perdido (dinheiro que ficou com a Tassia).
alter table pagamentos drop constraint if exists pagamentos_tipo_check;
alter table pagamentos add constraint pagamentos_tipo_check
    check (tipo = any (array['sinal','restante','total','sinal_retido']));

-- flag opcional: marca atendimento como pago integralmente adiantado
alter table atendimentos add column if not exists pago_integral boolean default false;


-- ----------------------------------------------------------------------------
-- 2. Tabela de créditos da cliente (sinal preservado de um cancelamento)
-- ----------------------------------------------------------------------------
-- Um crédito nasce quando a Tassia, ao cancelar, decide guardar o sinal.
-- Fica ligado à CLIENTE (não ao atendimento cancelado) até ser aplicado
-- num atendimento futuro (com confirmação) ou perdido depois.
create table if not exists creditos_cliente (
    id uuid primary key default gen_random_uuid(),
    cliente_id uuid not null references clientes(id) on delete cascade,
    valor numeric(10,2) not null,
    origem_atendimento_id uuid references atendimentos(id) on delete set null,
    status text not null default 'ativo',          -- ativo | aplicado | cancelado
    aplicado_atendimento_id uuid references atendimentos(id) on delete set null,
    criado_em timestamptz default now(),
    atualizado_em timestamptz default now()
);
create index if not exists idx_creditos_cliente on creditos_cliente (cliente_id, status);
alter table creditos_cliente enable row level security;


-- ----------------------------------------------------------------------------
-- 3. RPC: estado de pagamento de um atendimento
-- ----------------------------------------------------------------------------
-- Retorna total, pago (soma de sinal+restante+total), falta, e o estado.
create or replace function estado_pagamento(p_atendimento_id uuid)
returns table (
    total numeric,
    pago numeric,
    falta numeric,
    estado text,
    pago_integral boolean
)
language sql
stable
as $$
    with a as (
        select valor, coalesce(pago_integral, false) as pi
        from atendimentos where id = p_atendimento_id
    ),
    pg as (
        select coalesce(sum(valor), 0) as pago
        from pagamentos
        where atendimento_id = p_atendimento_id
          and tipo in ('sinal','restante','total')
    )
    select
        (select valor from a)                                          as total,
        (select pago from pg)                                          as pago,
        greatest((select valor from a) - (select pago from pg), 0)     as falta,
        case
            when (select pi from a) then 'integral'
            when (select pago from pg) <= 0 then 'sem_sinal'
            when (select pago from pg) >= (select valor from a) then 'integral'
            else 'parcial'
        end                                                            as estado,
        (select pi from a)                                             as pago_integral
    from a;
$$;


-- ----------------------------------------------------------------------------
-- 4. RPC: soma de sinais já pagos de um atendimento (pra exibição rápida)
-- ----------------------------------------------------------------------------
create or replace function sinal_do_atendimento(p_atendimento_id uuid)
returns numeric
language sql stable as $$
    select coalesce(sum(valor), 0)::numeric
    from pagamentos
    where atendimento_id = p_atendimento_id and tipo = 'sinal';
$$;


-- ----------------------------------------------------------------------------
-- 5. RPC: créditos ativos de uma cliente
-- ----------------------------------------------------------------------------
create or replace function creditos_ativos_cliente(p_cliente_id uuid)
returns table (id uuid, valor numeric, criado_em timestamptz)
language sql stable as $$
    select id, valor, criado_em
    from creditos_cliente
    where cliente_id = p_cliente_id and status = 'ativo'
    order by criado_em;
$$;


-- ----------------------------------------------------------------------------
-- 6. FATURAMENTO AJUSTADO: concluídos (valor no dia) + sinais retidos (no dia
--    da retenção). Substitui a métrica pra incluir receita de sinal perdido
--    sem duplicar.
-- ----------------------------------------------------------------------------
create or replace function metrica_faturamento(p_inicio date, p_fim date)
returns table (
    faturamento numeric,
    qtd_atendimentos bigint,
    ticket_medio numeric,
    receita_atendimentos numeric,
    receita_sinais_retidos numeric
)
language sql
stable
as $$
    with concl as (
        select coalesce(sum(valor), 0)::numeric as v, count(*)::bigint as n,
               coalesce(round(avg(valor), 2), 0)::numeric as tm
        from atendimentos
        where status = 'concluido' and data >= p_inicio and data <= p_fim
    ),
    retidos as (
        select coalesce(sum(valor), 0)::numeric as v
        from pagamentos
        where tipo = 'sinal_retido' and data >= p_inicio and data <= p_fim
    )
    select
        ((select v from concl) + (select v from retidos))::numeric   as faturamento,
        (select n from concl)                                        as qtd_atendimentos,
        (select tm from concl)                                       as ticket_medio,
        (select v from concl)                                        as receita_atendimentos,
        (select v from retidos)                                      as receita_sinais_retidos;
$$;


-- ----------------------------------------------------------------------------
-- 7. MÉTRICAS DE SINAL (as 7 úteis definidas com o Paulo)
-- ----------------------------------------------------------------------------
-- 7.1: total de sinais recebidos no período (pagamentos tipo sinal)
--      + ticket de sinal médio
create or replace function metrica_sinais_recebidos(p_inicio date, p_fim date)
returns table (
    total_recebido numeric,
    qtd_sinais bigint,
    ticket_sinal_medio numeric
)
language sql stable as $$
    select
        coalesce(sum(valor), 0)::numeric               as total_recebido,
        count(*)::bigint                               as qtd_sinais,
        coalesce(round(avg(valor), 2), 0)::numeric     as ticket_sinal_medio
    from pagamentos
    where tipo = 'sinal' and data >= p_inicio and data <= p_fim;
$$;

-- 7.2: sinais retidos por perda no período (receita de no-show)
create or replace function metrica_sinais_retidos(p_inicio date, p_fim date)
returns table (total_retido numeric, qtd bigint)
language sql stable as $$
    select coalesce(sum(valor),0)::numeric, count(*)::bigint
    from pagamentos
    where tipo = 'sinal_retido' and data >= p_inicio and data <= p_fim;
$$;

-- 7.3: créditos guardados ativos (dinheiro que a Tassia "deve" em serviço)
create or replace function metrica_creditos_ativos()
returns table (total_creditos numeric, qtd_clientes bigint)
language sql stable as $$
    select coalesce(sum(valor),0)::numeric, count(distinct cliente_id)::bigint
    from creditos_cliente where status = 'ativo';
$$;

-- 7.4: agendamentos futuros com sinal pago + total a receber (previsão caixa)
create or replace function metrica_agendamentos_com_sinal()
returns table (
    qtd_agendamentos bigint,
    total_sinais_pagos numeric,
    total_a_receber numeric
)
language sql stable as $$
    with fut as (
        select a.id, a.valor,
               coalesce((select sum(p.valor) from pagamentos p
                         where p.atendimento_id = a.id and p.tipo = 'sinal'), 0) as sinal
        from atendimentos a
        where a.status = 'agendado' and a.data >= current_date
    ),
    com_sinal as (select * from fut where sinal > 0)
    select
        count(*)::bigint                                          as qtd_agendamentos,
        coalesce(sum(sinal), 0)::numeric                          as total_sinais_pagos,
        coalesce(sum(greatest(valor - sinal, 0)), 0)::numeric     as total_a_receber
    from com_sinal;
$$;

-- 7.5: taxa de sinal (% de agendamentos futuros que têm sinal)
create or replace function metrica_taxa_sinal()
returns table (com_sinal bigint, total bigint, taxa_pct numeric)
language sql stable as $$
    with fut as (
        select a.id,
               coalesce((select sum(p.valor) from pagamentos p
                         where p.atendimento_id = a.id and p.tipo = 'sinal'), 0) as sinal
        from atendimentos a
        where a.status = 'agendado' and a.data >= current_date
    )
    select
        count(*) filter (where sinal > 0)::bigint                                   as com_sinal,
        count(*)::bigint                                                            as total,
        case when count(*) > 0
             then round(count(*) filter (where sinal > 0)::numeric / count(*) * 100, 0)
             else 0 end                                                             as taxa_pct
    from fut;
$$;

-- 7.6: créditos aplicados vs perdidos (comportamento das clientes)
create or replace function metrica_creditos_resultado(p_inicio date, p_fim date)
returns table (aplicados bigint, valor_aplicado numeric, perdidos bigint, valor_perdido numeric)
language sql stable as $$
    select
        count(*) filter (where status = 'aplicado')::bigint,
        coalesce(sum(valor) filter (where status = 'aplicado'), 0)::numeric,
        (select count(*) from pagamentos where tipo='sinal_retido' and data>=p_inicio and data<=p_fim)::bigint,
        (select coalesce(sum(valor),0) from pagamentos where tipo='sinal_retido' and data>=p_inicio and data<=p_fim)::numeric
    from creditos_cliente
    where atualizado_em::date >= p_inicio and atualizado_em::date <= p_fim;
$$;
