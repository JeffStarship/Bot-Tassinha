-- ============================================================================
-- Bot Tassinha — SQL da Sessão 8 (campanha de indicações)
-- ============================================================================
-- Idempotente. Cole no SQL Editor do Supabase e rode.
--
-- MODELO (decidido com o Paulo):
--   - Total indicado   = quantas pessoas a cliente indicou (clientes com
--                        indicada_por_id apontando pra ela).
--   - Total convertido = dessas, quantas já fizeram a 1ª visita
--                        (primeira_visita não nula).
--   - Ativas           = convertidas cuja primeira_visita foi há <= 2 meses.
--                        Este é o número que vale pra campanha agora.
--
-- A validade SEMPRE conta da primeira_visita (a conversão). A data do
-- registro da indicação não entra no cálculo de validade.
-- A janela de validade é configurável (config: indicacao_validade_meses=2).
-- ============================================================================


-- Garante o parâmetro de validade na config (default 2 meses)
insert into config (chave, valor) values ('indicacao_validade_meses', '2')
on conflict (chave) do nothing;


-- ----------------------------------------------------------------------------
-- 1. RPC: contadores de indicação de UMA cliente
-- ----------------------------------------------------------------------------
-- Usa indicada_por_id (quem indicou) e primeira_visita (se converteu e quando).

create or replace function indicacoes_cliente(p_indicadora_id uuid)
returns table (
    total_indicado bigint,
    total_convertido bigint,
    ativas bigint,
    validade_meses integer
)
language sql
stable
as $$
    with v as (
        select coalesce((select valor::int from config where chave = 'indicacao_validade_meses'), 2) as meses
    )
    select
        count(*)::bigint                                                   as total_indicado,
        count(*) filter (where ind.primeira_visita is not null)::bigint    as total_convertido,
        count(*) filter (
            where ind.primeira_visita is not null
              and ind.primeira_visita >= (current_date - make_interval(months => (select meses from v)))::date
        )::bigint                                                          as ativas,
        (select meses from v)                                             as validade_meses
    from clientes ind
    where ind.indicada_por_id = p_indicadora_id;
$$;


-- ----------------------------------------------------------------------------
-- 2. RPC: lista das indicações ATIVAS de uma cliente (nomes + data)
-- ----------------------------------------------------------------------------
-- Pra quando a Tassia quiser ver QUAIS estão valendo agora.

create or replace function indicacoes_ativas_cliente(p_indicadora_id uuid)
returns table (
    indicada_id uuid,
    nome text,
    primeira_visita date,
    dias_restantes integer
)
language sql
stable
as $$
    with v as (
        select coalesce((select valor::int from config where chave = 'indicacao_validade_meses'), 2) as meses
    )
    select
        ind.id,
        ind.nome,
        ind.primeira_visita,
        ( (ind.primeira_visita + make_interval(months => (select meses from v)))::date - current_date )::integer as dias_restantes
    from clientes ind
    where ind.indicada_por_id = p_indicadora_id
      and ind.primeira_visita is not null
      and ind.primeira_visita >= (current_date - make_interval(months => (select meses from v)))::date
    order by ind.primeira_visita desc;
$$;


-- ----------------------------------------------------------------------------
-- 3. RPC: ranking de quem mais indicou (convertidas)
-- ----------------------------------------------------------------------------
-- Ordena pelas convertidas na vida (quem realmente trouxe clientes).

create or replace function ranking_indicadoras(p_limite integer default 10)
returns table (
    indicadora_id uuid,
    nome text,
    total_indicado bigint,
    total_convertido bigint,
    ativas bigint
)
language sql
stable
as $$
    with v as (
        select coalesce((select valor::int from config where chave = 'indicacao_validade_meses'), 2) as meses
    )
    select
        ic.id,
        ic.nome,
        count(ind.id)::bigint                                              as total_indicado,
        count(ind.id) filter (where ind.primeira_visita is not null)::bigint as total_convertido,
        count(ind.id) filter (
            where ind.primeira_visita is not null
              and ind.primeira_visita >= (current_date - make_interval(months => (select meses from v)))::date
        )::bigint                                                          as ativas
    from clientes ic
    join clientes ind on ind.indicada_por_id = ic.id
    group by ic.id, ic.nome
    having count(ind.id) > 0
    order by count(ind.id) filter (where ind.primeira_visita is not null) desc,
             count(ind.id) desc
    limit p_limite;
$$;
