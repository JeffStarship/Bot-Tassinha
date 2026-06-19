-- ============================================================================
-- Bot Tassinha — SQL da Sessão 6 (lembretes de início/fim do atendimento)
-- ============================================================================
-- Idempotente. Cole no SQL Editor do Supabase e rode.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1. Flags de controle de aviso (idempotência)
-- ----------------------------------------------------------------------------
-- avisado_inicio / avisado_fim: marcam que o lembrete já foi enviado, pra o
-- job de 5 em 5 min não repetir.
-- inicio_segurado: quando a Tassia diz "ainda não começou", trava o disparo
-- até ela confirmar o início real.

alter table atendimentos add column if not exists avisado_inicio boolean default false;
alter table atendimentos add column if not exists avisado_fim boolean default false;
alter table atendimentos add column if not exists inicio_segurado boolean default false;


-- ----------------------------------------------------------------------------
-- 2. RPC: atendimentos que estão COMEÇANDO agora
-- ----------------------------------------------------------------------------
-- Retorna atendimentos de hoje, status agendado, cuja hora prevista já chegou
-- (dentro de uma janela de tolerância), que ainda não foram avisados de início
-- e não estão segurados. O scheduler chama isso a cada 5 min.
-- p_agora = horário atual (HH:MM:SS) no fuso de São Paulo, passado pelo Python.

create or replace function atendimentos_comecando(p_hoje date, p_agora time, p_tolerancia_min integer default 5)
returns table (
    id uuid,
    cliente_id uuid,
    nome text,
    servico text,
    hora time,
    duracao_min integer
)
language sql
stable
as $$
    select
        a.id, a.cliente_id, cl.nome, a.servico, a.hora, a.duracao_min
    from atendimentos a
    join clientes cl on cl.id = a.cliente_id
    where a.data = p_hoje
      and a.status = 'agendado'
      and a.hora is not null
      and a.avisado_inicio = false
      and a.inicio_segurado = false
      -- hora prevista já passou, mas não faz mais de (tolerancia) que passou
      and date_trunc('minute', a.hora::interval) <= date_trunc('minute', p_agora::interval)
      and date_trunc('minute', a.hora::interval) >= date_trunc('minute', (p_agora - make_interval(mins => p_tolerancia_min))::interval);
$$;


-- ----------------------------------------------------------------------------
-- 3. RPC: atendimentos que estão ACABANDO agora
-- ----------------------------------------------------------------------------
-- Já tiveram o início avisado; passou a duração (default do próprio atendimento
-- ou 90); ainda não foram avisados de fim. Conta a partir de inicio_real se
-- houver (Tassia confirmou hora real), senão a partir da hora prevista de hoje.

create or replace function atendimentos_acabando(p_hoje date, p_agora_ts timestamptz, p_tolerancia_min integer default 5)
returns table (
    id uuid,
    cliente_id uuid,
    nome text,
    servico text,
    fim_previsto timestamptz
)
language sql
stable
as $$
    with base as (
        select
            a.id, a.cliente_id, cl.nome, a.servico,
            coalesce(a.duracao_min,
                     (select valor::int from config where chave = 'duracao_padrao_min'),
                     90) as dur,
            -- timestamp de início: inicio_real se houver, senão data+hora prevista
            coalesce(
                a.inicio_real,
                (a.data + a.hora) at time zone 'America/Sao_Paulo'
            ) as inicio_ts
        from atendimentos a
        join clientes cl on cl.id = a.cliente_id
        where a.data = p_hoje
          and a.status = 'agendado'
          and a.avisado_inicio = true
          and a.avisado_fim = false
          and a.hora is not null
    )
    select
        id, cliente_id, nome, servico,
        (inicio_ts + make_interval(mins => dur)) as fim_previsto
    from base
    where date_trunc('minute', inicio_ts + make_interval(mins => dur)) <= date_trunc('minute', p_agora_ts)
      and date_trunc('minute', inicio_ts + make_interval(mins => dur)) >= date_trunc('minute', p_agora_ts - make_interval(mins => p_tolerancia_min));
$$;


-- ----------------------------------------------------------------------------
-- 4. RPCs de marcação (chamadas pelo scheduler após enviar)
-- ----------------------------------------------------------------------------
create or replace function marcar_avisado_inicio(p_id uuid)
returns void language sql as $$
    update atendimentos set avisado_inicio = true where id = p_id;
$$;

create or replace function marcar_avisado_fim(p_id uuid)
returns void language sql as $$
    update atendimentos set avisado_fim = true where id = p_id;
$$;
