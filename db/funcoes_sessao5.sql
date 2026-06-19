-- ============================================================================
-- Bot Tassinha — Schema da Sessão 5 (hora no agendamento + serviços/preços)
-- ============================================================================
-- Idempotente. Cole no SQL Editor do Supabase e rode.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1. Hora e início real nos atendimentos
-- ----------------------------------------------------------------------------
-- hora        = horário previsto do agendamento (HH:MM)
-- inicio_real = quando o atendimento começou de fato (preenchido na Sessão 6
--               quando a Tassia confirma início; default = hora prevista)
-- Ambos nullable: agendamentos/atendimentos antigos sem hora não quebram.

alter table atendimentos add column if not exists hora time;
alter table atendimentos add column if not exists inicio_real timestamptz;


-- ----------------------------------------------------------------------------
-- 2. Tabela de serviços (catálogo de preços)
-- ----------------------------------------------------------------------------
-- A Tassia cadastra em texto livre; a IA interpreta e popula esta tabela.
-- nome é único (case-insensitive via índice) pra reenvio substituir.

create table if not exists servicos (
    id uuid primary key default gen_random_uuid(),
    nome text not null,
    preco numeric(10,2) not null,
    duracao_min integer not null default 90,
    ativo boolean not null default true,
    criado_em timestamptz default now(),
    atualizado_em timestamptz default now()
);

-- Unicidade case-insensitive do nome (pra "Manutenção" == "manutencao" não
-- duplicar quando ela reenvia). Usa lower + unaccent não está disponível por
-- padrão, então normaliza só por lower aqui.
create unique index if not exists idx_servicos_nome_lower
    on servicos (lower(nome));

alter table servicos enable row level security;


-- ----------------------------------------------------------------------------
-- 3. Configuração chave-valor (duração padrão configurável, etc)
-- ----------------------------------------------------------------------------
-- Fundação pra valores ajustáveis sem mexer em código. Começa com a
-- duração padrão do atendimento (90 min = 1h30, pedido da Tassia).

create table if not exists config (
    chave text primary key,
    valor text not null,
    atualizado_em timestamptz default now()
);

alter table config enable row level security;

insert into config (chave, valor)
values ('duracao_padrao_min', '90')
on conflict (chave) do nothing;


-- ----------------------------------------------------------------------------
-- 4. RPC: upsert de serviço (substitui se o nome já existe)
-- ----------------------------------------------------------------------------
-- Recebe nome+preço (+duração opcional). Se já existe (case-insensitive),
-- atualiza preço/duração e reativa. Senão cria. Retorna a linha final.

create or replace function upsert_servico(
    p_nome text,
    p_preco numeric,
    p_duracao_min integer default null
)
returns table (id uuid, nome text, preco numeric, duracao_min integer, ativo boolean, acao text)
language plpgsql
as $$
declare
    v_existente servicos%rowtype;
    v_dur integer;
begin
    select * into v_existente from servicos where lower(servicos.nome) = lower(p_nome) limit 1;

    -- duração: usa a informada, senão mantém a existente, senão o default da config
    if p_duracao_min is not null then
        v_dur := p_duracao_min;
    elsif v_existente.id is not null then
        v_dur := v_existente.duracao_min;
    else
        v_dur := coalesce((select valor::int from config where chave = 'duracao_padrao_min'), 90);
    end if;

    if v_existente.id is not null then
        update servicos
           set preco = p_preco,
               duracao_min = v_dur,
               ativo = true,
               atualizado_em = now()
         where servicos.id = v_existente.id;
        return query select v_existente.id, p_nome, p_preco, v_dur, true, 'atualizado'::text;
    else
        insert into servicos (nome, preco, duracao_min)
        values (p_nome, p_preco, v_dur)
        returning servicos.id into v_existente;
        return query select v_existente.id, p_nome, p_preco, v_dur, true, 'criado'::text;
    end if;
end;
$$;


-- ----------------------------------------------------------------------------
-- 5. RPC: buscar preço de um serviço pelo nome (parcial, case-insensitive)
-- ----------------------------------------------------------------------------
-- Usada quando a Tassia registra atendimento dizendo só o nome do serviço.
-- Retorna 0, 1 ou mais matches (o agente desambigua se vier mais de um).

create or replace function buscar_servico(p_nome text)
returns table (id uuid, nome text, preco numeric, duracao_min integer)
language sql
stable
as $$
    select id, nome, preco, duracao_min
    from servicos
    where ativo = true
      and nome ilike '%' || p_nome || '%'
    order by nome;
$$;
