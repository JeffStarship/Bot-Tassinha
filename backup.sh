#!/usr/bin/env bash
# ============================================================================
# Bot Tassinha — Backup diário do banco (Sessão 14)
# ============================================================================
# Faz pg_dump do banco Supabase, comprime, guarda os últimos 7 dias no Contabo
# e (opcional) manda cópia pro Google Drive via rclone.
#
# Agendado via cron (1x por dia). Ver instruções de instalação no fim do arquivo.
#
# Requer no .env (mesmo do bot):
#   SUPABASE_DB_URL  -> connection string do Postgres do Supabase
#      (Supabase: Project Settings > Database > Connection string > URI,
#       usar a "Session pooler" ou "Direct connection"; formato:
#       postgresql://postgres:[SENHA]@db.xxxx.supabase.co:5432/postgres)
#
# Opcional pro Drive:
#   RCLONE_REMOTE    -> nome do remote do rclone + pasta (ex: "gdrive:BackupsTassinha")
#      Se vazio ou rclone não instalado, faz só o backup local (sem erro).
# ============================================================================

set -euo pipefail

# --- diretórios ---
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="${BASE_DIR}/backups"
mkdir -p "$BACKUP_DIR"

# --- carrega variáveis do .env ---
if [ -f "${BASE_DIR}/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "${BASE_DIR}/.env"
    set +a
fi

if [ -z "${SUPABASE_DB_URL:-}" ]; then
    echo "[backup] ERRO: SUPABASE_DB_URL não definida no .env. Abortando." >&2
    exit 1
fi

# --- nome do arquivo com data ---
DATA="$(date +%Y-%m-%d_%H%M)"
ARQUIVO="${BACKUP_DIR}/tassinha_${DATA}.sql.gz"

echo "[backup] Iniciando dump em ${ARQUIVO}"

# --- dump + compressão ---
# --no-owner e --no-privileges pra restore limpo em qualquer banco.
if pg_dump "$SUPABASE_DB_URL" --no-owner --no-privileges 2>/dev/null | gzip > "$ARQUIVO"; then
    TAMANHO="$(du -h "$ARQUIVO" | cut -f1)"
    echo "[backup] Dump OK (${TAMANHO})"
else
    echo "[backup] ERRO no pg_dump. Removendo arquivo parcial." >&2
    rm -f "$ARQUIVO"
    exit 1
fi

# --- valida que o backup não está vazio/corrompido ---
if ! gzip -t "$ARQUIVO" 2>/dev/null; then
    echo "[backup] ERRO: arquivo corrompido. Removendo." >&2
    rm -f "$ARQUIVO"
    exit 1
fi

# --- cópia pro Google Drive (se configurado) ---
if [ -n "${RCLONE_REMOTE:-}" ] && command -v rclone >/dev/null 2>&1; then
    echo "[backup] Enviando pro Drive (${RCLONE_REMOTE})"
    if rclone copy "$ARQUIVO" "$RCLONE_REMOTE" 2>/dev/null; then
        echo "[backup] Cópia no Drive OK"
    else
        echo "[backup] AVISO: falha ao copiar pro Drive (backup local está salvo)" >&2
    fi
else
    echo "[backup] Drive não configurado (RCLONE_REMOTE vazio ou rclone ausente) — só backup local"
fi

# --- retenção: mantém os últimos 7 dias localmente ---
echo "[backup] Limpando backups locais com mais de 7 dias"
find "$BACKUP_DIR" -name "tassinha_*.sql.gz" -mtime +7 -delete

# --- retenção no Drive (se configurado): mantém 30 dias ---
if [ -n "${RCLONE_REMOTE:-}" ] && command -v rclone >/dev/null 2>&1; then
    rclone delete --min-age 30d "$RCLONE_REMOTE" 2>/dev/null || true
fi

echo "[backup] Concluído: ${ARQUIVO}"

# ============================================================================
# INSTALAÇÃO (fazer uma vez):
#
# 1. Instalar o cliente Postgres (pg_dump) no Contabo:
#      sudo apt update && sudo apt install -y postgresql-client
#
# 2. Pegar a connection string no Supabase:
#      Project Settings > Database > Connection string > URI
#      Adicionar no .env do bot:
#      SUPABASE_DB_URL=postgresql://postgres:SENHA@db.xxxx.supabase.co:5432/postgres
#
# 3. Dar permissão de execução:
#      chmod +x ~/Bot-Tassinha/backup.sh
#
# 4. Testar rodando na mão:
#      ~/Bot-Tassinha/backup.sh
#
# 5. Agendar no cron (todo dia às 3h da manhã, horário do servidor):
#      crontab -e
#      e adicionar a linha:
#      0 3 * * * /root/Bot-Tassinha/backup.sh >> /root/Bot-Tassinha/backups/backup.log 2>&1
#
# 6. (Depois, pro Drive) instalar e configurar o rclone — ver GUIA no chat.
# ============================================================================
