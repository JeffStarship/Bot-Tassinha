-- ============================================================================
-- Bot Tassinha — SQL da Sessão 11 (estoque inteligente / produtos)
-- ============================================================================
-- Idempotente. Cole no SQL Editor do Supabase e rode.
--
-- MODELO (decidido com o Paulo):
--   - A Tassia registra COMPRAS (não saídas). O consumo é INFERIDO do
--     intervalo entre recompras.
--   - Cada recompra fecha um ciclo: quanto durou em DIAS e em ATENDIMENTOS
--     (atendimentos concluídos no intervalo). A previsão é a média dos ciclos
--     fechados, com confiança crescente conforme há mais recompras.
--   - Eixo PRIMÁRIO = atendimentos; secundário = tempo.
--   - Quantidade importa: 3 potes duram 3x 1 pote. Normalizamos por unidade.
--   - Loja é registrada por compra -> histórico/comparação de preço por lugar.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1. Produtos
-- ----------------------------------------------------------------------------
create table if not exists produtos (
    id uuid primary key default gen_random_uuid(),
    nome text not null,
    unidade text,                       -- ex: 'pote', 'frasco', 'unidade' (livre)
    ativo boolean not null default true,
    criado_em timestamptz default now()
);
create unique index if not exists idx_produtos_nome_lower on produtos (lower(nome));
alter table produtos enable row level security;


-- ----------------------------------------------------------------------------
-- 2. Compras (entradas)
-- ----------------------------------------------------------------------------
create table if not exists compras (
    id uuid primary key default gen_random_uuid(),
    produto_id uuid not null references produtos(id) on delete cascade,
    data date not null,
    quantidade numeric(10,2) not null default 1,   -- quantas unidades
    preco_unitario numeric(10,2) not null,         -- preço por unidade
    loja text,                                      -- onde comprou
    criado_em timestamptz default now()
);
create index if not exists idx_compras_produto on compras (produto_id, data);
alter table compras enable row level security;


-- ----------------------------------------------------------------------------
-- 3. RPC: registrar produto (upsert por nome)
-- ----------------------------------------------------------------------------
create or replace function upsert_produto(p_nome text, p_unidade text default null)
returns table (id uuid, nome text, unidade text, acao text)
language plpgsql
as $$
declare
    v_existente produtos%rowtype;
begin
    select * into v_existente from produtos where lower(produtos.nome) = lower(p_nome) limit 1;
    if v_existente.id is not null then
        update produtos set unidade = coalesce(p_unidade, produtos.unidade), ativo = true
         where produtos.id = v_existente.id;
        return query select v_existente.id, p_nome, coalesce(p_unidade, v_existente.unidade), 'atualizado'::text;
    else
        insert into produtos (nome, unidade) values (p_nome, p_unidade)
        returning produtos.id into v_existente;
        return query select v_existente.id, p_nome, p_unidade, 'criado'::text;
    end if;
end;
$$;


-- ----------------------------------------------------------------------------
-- 4. RPC: registrar compra (cria produto se não existir é feito no Python)
-- ----------------------------------------------------------------------------
-- (a inserção em si é via supabase-py; aqui só uma helper de busca de produto)
create or replace function buscar_produto(p_nome text)
returns table (id uuid, nome text, unidade text)
language sql stable as $$
    select id, nome, unidade from produtos
    where ativo = true and nome ilike '%' || p_nome || '%'
    order by nome;
$$;


-- ----------------------------------------------------------------------------
-- 5. RPC: previsão de recompra de UM produto
-- ----------------------------------------------------------------------------
-- Calcula, a partir do histórico de compras, quanto cada "unidade" costuma
-- durar em ATENDIMENTOS e em DIAS, e projeta o consumo desde a última compra.
--
-- Ciclo = intervalo entre uma compra e a próxima. Em cada ciclo medimos:
--   dias_ciclo        = dias entre as duas compras
--   atend_ciclo       = atendimentos concluídos nesse intervalo
--   unidades_compradas na compra que ABRE o ciclo (pra normalizar por unidade)
-- Normaliza: dura_por_unidade = ciclo / unidades da compra que abriu o ciclo.
-- Previsão = média dos ciclos normalizados. Confiança pela qtd de ciclos.

create or replace function previsao_produto(p_produto_id uuid)
returns table (
    nome text,
    compras_registradas bigint,
    ciclos_fechados bigint,
    confianca text,
    media_atend_por_unidade numeric,
    media_dias_por_unidade numeric,
    ultima_compra date,
    ultima_qtd numeric,
    atend_desde_ultima bigint,
    dias_desde_ultima integer,
    consumo_estimado_pct numeric
)
language sql
stable
as $$
    with compras_ord as (
        select
            c.data,
            c.quantidade,
            lag(c.data) over (order by c.data)       as data_anterior,
            lag(c.quantidade) over (order by c.data) as qtd_anterior
        from compras c
        where c.produto_id = p_produto_id
    ),
    -- ciclos fechados: cada compra (menos a 1ª) fecha o ciclo aberto pela anterior
    ciclos as (
        select
            (data - data_anterior)                                    as dias_ciclo,
            (select count(*) from atendimentos a
              where a.status = 'concluido'
                and a.data > co.data_anterior and a.data <= co.data)   as atend_ciclo,
            greatest(qtd_anterior, 1)                                  as unidades_abriu
        from compras_ord co
        where data_anterior is not null
    ),
    medias as (
        select
            count(*)                                                  as n_ciclos,
            avg(atend_ciclo::numeric / unidades_abriu)                as atend_por_unid,
            avg(dias_ciclo::numeric / unidades_abriu)                 as dias_por_unid
        from ciclos
    ),
    ult as (
        select data as ultima, quantidade as qtd
        from compras where produto_id = p_produto_id
        order by data desc limit 1
    ),
    desde as (
        select
            (select count(*) from atendimentos a
              where a.status = 'concluido' and a.data > (select ultima from ult)) as atend_desde,
            (current_date - (select ultima from ult))                             as dias_desde
    ),
    prod as (select nome from produtos where id = p_produto_id),
    total as (select count(*) as n from compras where produto_id = p_produto_id)
    select
        (select nome from prod),
        (select n from total),
        (select n_ciclos from medias),
        case
            when (select n_ciclos from medias) >= 4 then 'solida'
            when (select n_ciclos from medias) >= 2 then 'media'
            else 'fraca'
        end,
        round((select atend_por_unid from medias), 1),
        round((select dias_por_unid from medias), 1),
        (select ultima from ult),
        (select qtd from ult),
        (select atend_desde from desde),
        (select dias_desde from desde),
        -- consumo estimado: atend desde a última / (atend_por_unid * qtd da última)
        case
            when (select atend_por_unid from medias) is not null
                 and (select atend_por_unid from medias) > 0
                 and (select qtd from ult) > 0
            then round(
                (select atend_desde from desde)::numeric
                / ((select atend_por_unid from medias) * (select qtd from ult)) * 100, 0)
            else null
        end
    from prod;
$$;


-- ----------------------------------------------------------------------------
-- 6. RPC: produtos que precisam de reposição (pro resumo de domingo)
-- ----------------------------------------------------------------------------
-- Lista produtos cujo consumo estimado passou de um limiar (default 75%).
-- Só considera produtos com pelo menos 2 ciclos (confiança média+).

create or replace function produtos_para_repor(p_limiar_pct numeric default 75)
returns table (
    produto_id uuid,
    nome text,
    confianca text,
    consumo_estimado_pct numeric,
    media_atend_por_unidade numeric,
    atend_desde_ultima bigint
)
language sql
stable
as $$
    select
        p.id, pr.nome, pr.confianca, pr.consumo_estimado_pct,
        pr.media_atend_por_unidade, pr.atend_desde_ultima
    from produtos p
    cross join lateral previsao_produto(p.id) pr
    where p.ativo = true
      and pr.ciclos_fechados >= 2
      and pr.consumo_estimado_pct is not null
      and pr.consumo_estimado_pct >= p_limiar_pct
    order by pr.consumo_estimado_pct desc;
$$;


-- ----------------------------------------------------------------------------
-- 7. RPC: gasto por produto + histórico de preço por loja
-- ----------------------------------------------------------------------------
create or replace function gasto_produto(p_produto_id uuid)
returns table (
    total_gasto numeric,
    total_unidades numeric,
    compras bigint
)
language sql stable as $$
    select
        coalesce(sum(quantidade * preco_unitario), 0)::numeric,
        coalesce(sum(quantidade), 0)::numeric,
        count(*)::bigint
    from compras where produto_id = p_produto_id;
$$;


create or replace function precos_por_loja(p_produto_id uuid)
returns table (
    loja text,
    ultimo_preco numeric,
    menor_preco numeric,
    ultima_compra date
)
language sql stable as $$
    select
        coalesce(loja, 'não informado') as loja,
        (array_agg(preco_unitario order by data desc))[1] as ultimo_preco,
        min(preco_unitario) as menor_preco,
        max(data) as ultima_compra
    from compras
    where produto_id = p_produto_id
    group by coalesce(loja, 'não informado')
    order by ultimo_preco asc;
$$;
