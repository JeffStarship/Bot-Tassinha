# Bot Tassinha

CRM conversacional via Telegram para gestão do negócio de alongamento
de unhas em soft gel. Operador-only — só Tassinha e Paulo usam.

## Status do projeto

- [x] Sessão 1 — Schema do Supabase + estrutura do projeto
- [x] Sessão 2 — Bot base + ferramentas CRUD
- [ ] Sessão 3 — Ferramentas de consulta + métricas
- [ ] Sessão 4 — Jobs proativos
- [ ] Sessão 5 — Polimento e entrega

## Stack

- Telegram (interface)
- Gemini Flash (IA — entende texto, chama ferramentas, narra resultado)
- Supabase / Postgres (banco — tudo que é fato vem daqui)
- Docker no Contabo VPS (hosting)

## Como rodar

1. Copie `.env.example` para `.env` e preencha todas as variáveis
2. `docker compose up -d --build`
3. Veja os logs: `docker compose logs -f bot`

## Princípio de funcionamento

A IA nunca calcula nem inventa número. Ela só traduz o que a pessoa
escreveu em chamadas de ferramenta, e narra o que a ferramenta
devolveu. Todo fato vem de uma consulta real ao banco.
