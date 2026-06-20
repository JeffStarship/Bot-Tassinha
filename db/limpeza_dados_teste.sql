-- ============================================================================
-- Bot Tassinha — Limpeza de dados de teste (rodar ANTES do uso real)
-- ============================================================================
-- Apaga TODOS os dados das tabelas, deixando o banco zerado pra Tassia começar
-- do zero. NÃO apaga as tabelas nem as funções — só o conteúdo.
--
-- A ordem respeita as ligações entre tabelas (apaga "filhos" antes dos "pais").
-- Roda no SQL Editor do Supabase. Resultado esperado: "Success. No rows returned".
--
-- ATENÇÃO: isto é irreversível. Só rode quando tiver certeza de que quer zerar
-- tudo (o que é o caso antes de entregar pra Tassia usar de verdade).
-- ============================================================================

delete from preferencias_cliente;
delete from pagamentos;
delete from indicacoes;
delete from atendimentos;
delete from despesas;
delete from clientes;

-- Os serviços (catálogo de preços) e a config NÃO são apagados de propósito:
-- se a Tassia já cadastrou os preços reais dela, eles continuam.
-- Se quiser zerar o catálogo também, descomente a linha abaixo:
-- delete from servicos;
