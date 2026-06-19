-- ============================================================================
-- Bot Tassinha — SQL da Sessão 7 (follow-ups + cancelamento)
-- ============================================================================
-- Idempotente. Cole no SQL Editor do Supabase e rode.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1. Flags de follow-up nos atendimentos
-- ----------------------------------------------------------------------------
-- avisado_followup_1d : lembrete de 1 dia antes já enviado
-- avisado_followup_3h : lembrete de 3h antes já enviado
-- precisa_reagendar   : cliente cancelou e ainda não reagendou (aparece no
--                       diário das 9h até reagendar ou a Tassia mandar parar)

alter table atendimentos add column if not exists avisado_followup_1d boolean default false;
alter table atendimentos add column if not exists avisado_followup_3h boolean default false;
alter table atendimentos add column if not exists precisa_reagendar boolean default false;


-- ----------------------------------------------------------------------------
-- 2. Mensagens-padrão de follow-up na config
-- ----------------------------------------------------------------------------
-- Editáveis pela Tassia ("muda a mensagem de 1 dia antes pra..."). {nome} e
-- {hora} são preenchidos na hora do disparo.

insert into config (chave, valor) values
  ('followup_1d_msg', 'Oi {nome}! Passando pra confirmar seu horário amanhã às {hora}. Posso confirmar?'),
  ('followup_3h_msg', 'Oi {nome}! Lembrete do seu horário hoje às {hora}. Está tudo certo?')
on conflict (chave) do nothing;


-- ----------------------------------------------------------------------------
-- 3. RPC: follow-ups de 1 DIA antes (rodado de manhã)
-- ----------------------------------------------------------------------------
-- Atendimentos agendados pra AMANHÃ, com hora e telefone, que ainda não
-- receberam o follow-up de 1 dia. Retorna telefone pro botão wa.me.

create or replace function followups_1dia(p_hoje date)
returns table (
    id uuid,
    cliente_id uuid,
    nome text,
    telefone text,
    servico text,
    hora time
)
language sql
stable
as $$
    select a.id, a.cliente_id, cl.nome, cl.telefone, a.servico, a.hora
    from atendimentos a
    join clientes cl on cl.id = a.cliente_id
    where a.data = p_hoje + 1
      and a.status = 'agendado'
      and a.hora is not null
      and a.avisado_followup_1d = false;
$$;


-- ----------------------------------------------------------------------------
-- 4. RPC: follow-ups de 3 HORAS antes (rodado de 5 em 5 min)
-- ----------------------------------------------------------------------------
-- Atendimentos de hoje cuja hora está a ~3h de distância (janela de 5 min),
-- com telefone, ainda não avisados.

create or replace function followups_3horas(p_hoje date, p_agora time, p_tolerancia_min integer default 5)
returns table (
    id uuid,
    cliente_id uuid,
    nome text,
    telefone text,
    servico text,
    hora time
)
language sql
stable
as $$
    select a.id, a.cliente_id, cl.nome, cl.telefone, a.servico, a.hora
    from atendimentos a
    join clientes cl on cl.id = a.cliente_id
    where a.data = p_hoje
      and a.status = 'agendado'
      and a.hora is not null
      and a.avisado_followup_3h = false
      -- a hora menos 3h cai dentro da janela de agora
      and date_trunc('minute', (a.hora - interval '3 hours')) <= date_trunc('minute', p_agora::interval)
      and date_trunc('minute', (a.hora - interval '3 hours')) >= date_trunc('minute', (p_agora - make_interval(mins => p_tolerancia_min))::interval);
$$;


-- ----------------------------------------------------------------------------
-- 5. RPCs de marcação
-- ----------------------------------------------------------------------------
create or replace function marcar_followup_1d(p_id uuid)
returns void language sql as $$
    update atendimentos set avisado_followup_1d = true where id = p_id;
$$;

create or replace function marcar_followup_3h(p_id uuid)
returns void language sql as $$
    update atendimentos set avisado_followup_3h = true where id = p_id;
$$;


-- ----------------------------------------------------------------------------
-- 6. RPC: clientes que cancelaram e precisam reagendar
-- ----------------------------------------------------------------------------
-- Aparecem no resumo diário das 9h até reagendar ou a Tassia mandar parar.

create or replace function pendentes_reagendar()
returns table (
    cliente_id uuid,
    nome text,
    telefone text,
    servico text,
    data_cancelada date
)
language sql
stable
as $$
    select a.cliente_id, cl.nome, cl.telefone, a.servico, a.data
    from atendimentos a
    join clientes cl on cl.id = a.cliente_id
    where a.precisa_reagendar = true
    order by a.data;
$$;
