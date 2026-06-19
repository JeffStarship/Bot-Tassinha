-- ============================================================================
-- Bot Tassinha — SQL da Sessão 9 (preferências por cliente)
-- ============================================================================
-- Idempotente. Cole no SQL Editor do Supabase e rode.
--
-- Tabela GENÉRICA de preferências por cliente (chave-valor). Preparada pra
-- crescer: qualquer preferência futura entra como nova chave, sem migration.
--
-- Chaves usadas pelo follow-up:
--   followup_ativo      -> 'true' | 'false'  (default true = recebe)
--   followup_1d_texto   -> texto custom do lembrete de 1 dia
--   followup_3h_texto   -> texto custom do lembrete de N horas
--   followup_1d_dias    -> quantos DIAS antes (default 1)
--   followup_3h_horas   -> quantas HORAS antes (default 3)
-- Ausência de chave = usa o padrão global (config) / default.
-- ============================================================================


create table if not exists preferencias_cliente (
    cliente_id uuid not null references clientes(id) on delete cascade,
    chave text not null,
    valor text not null,
    atualizado_em timestamptz default now(),
    primary key (cliente_id, chave)
);

alter table preferencias_cliente enable row level security;


-- ----------------------------------------------------------------------------
-- RPC: upsert de uma preferência
-- ----------------------------------------------------------------------------
create or replace function definir_preferencia(p_cliente_id uuid, p_chave text, p_valor text)
returns void
language sql
as $$
    insert into preferencias_cliente (cliente_id, chave, valor, atualizado_em)
    values (p_cliente_id, p_chave, p_valor, now())
    on conflict (cliente_id, chave)
    do update set valor = excluded.valor, atualizado_em = now();
$$;


-- RPC: remove UMA preferência (volta ao padrão naquele aspecto)
create or replace function remover_preferencia(p_cliente_id uuid, p_chave text)
returns void
language sql
as $$
    delete from preferencias_cliente where cliente_id = p_cliente_id and chave = p_chave;
$$;


-- RPC: remove TODAS as preferências de follow-up de uma cliente (reset)
create or replace function resetar_followup_cliente(p_cliente_id uuid)
returns void
language sql
as $$
    delete from preferencias_cliente
    where cliente_id = p_cliente_id
      and chave in ('followup_ativo','followup_1d_texto','followup_3h_texto',
                    'followup_1d_dias','followup_3h_horas');
$$;


-- RPC: lê todas as preferências de uma cliente (pra Tassia conferir)
create or replace function preferencias_de(p_cliente_id uuid)
returns table (chave text, valor text)
language sql
stable
as $$
    select chave, valor from preferencias_cliente where cliente_id = p_cliente_id order by chave;
$$;


-- helper interno: pega o valor de uma preferência ou NULL
create or replace function _pref(p_cliente_id uuid, p_chave text)
returns text
language sql
stable
as $$
    select valor from preferencias_cliente where cliente_id = p_cliente_id and chave = p_chave;
$$;


-- ----------------------------------------------------------------------------
-- RPC: follow-ups de 1 DIA — agora respeita followup_1d_dias e followup_ativo
-- ----------------------------------------------------------------------------
-- Em vez de "amanhã" fixo, cada cliente pode ter N dias antes.
-- Cliente com followup_ativo='false' é excluída.

create or replace function followups_1dia(p_hoje date)
returns table (
    id uuid,
    cliente_id uuid,
    nome text,
    telefone text,
    servico text,
    hora time,
    dias_antes integer
)
language sql
stable
as $$
    select
        a.id, a.cliente_id, cl.nome, cl.telefone, a.servico, a.hora,
        coalesce(_pref(cl.id, 'followup_1d_dias')::int, 1) as dias_antes
    from atendimentos a
    join clientes cl on cl.id = a.cliente_id
    where a.status = 'agendado'
      and a.hora is not null
      and a.avisado_followup_1d = false
      and coalesce(_pref(cl.id, 'followup_ativo'), 'true') <> 'false'
      -- a data do atendimento é daqui a (dias_antes) dias
      and a.data = p_hoje + coalesce(_pref(cl.id, 'followup_1d_dias')::int, 1);
$$;


-- ----------------------------------------------------------------------------
-- RPC: follow-ups de N HORAS — respeita followup_3h_horas e followup_ativo
-- ----------------------------------------------------------------------------
create or replace function followups_3horas(p_hoje date, p_agora time, p_tolerancia_min integer default 5)
returns table (
    id uuid,
    cliente_id uuid,
    nome text,
    telefone text,
    servico text,
    hora time,
    horas_antes integer
)
language sql
stable
as $$
    select
        a.id, a.cliente_id, cl.nome, cl.telefone, a.servico, a.hora,
        coalesce(_pref(cl.id, 'followup_3h_horas')::int, 3) as horas_antes
    from atendimentos a
    join clientes cl on cl.id = a.cliente_id
    where a.data = p_hoje
      and a.status = 'agendado'
      and a.hora is not null
      and a.avisado_followup_3h = false
      and coalesce(_pref(cl.id, 'followup_ativo'), 'true') <> 'false'
      -- a hora menos (horas_antes) cai dentro da janela de agora
      and date_trunc('minute', (a.hora - make_interval(hours => coalesce(_pref(cl.id,'followup_3h_horas')::int, 3))))
            <= date_trunc('minute', p_agora::interval)
      and date_trunc('minute', (a.hora - make_interval(hours => coalesce(_pref(cl.id,'followup_3h_horas')::int, 3))))
            >= date_trunc('minute', (p_agora - make_interval(mins => p_tolerancia_min))::interval);
$$;
