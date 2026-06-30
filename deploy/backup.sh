#!/usr/bin/env bash
# backup.sh — ежедневный бэкап базы Re.form (на самом сервере).
#
# Делает сжатый дамп базы, хранит последние 14 дней, обновляет *_latest.sql.gz
# (его скачивает твой компьютер скриптом pull-backup.ps1).
#
# Ставится в CRON РУТА (чтобы pg_dump шёл от postgres без пароля):
#   sudo crontab -e
#   30 3 * * * /home/reform/reform/deploy/backup.sh >> /home/reform/backups/backup.log 2>&1
# (каждый день в 03:30 по Москве)
set -euo pipefail

DB="cosmo_db"                      # ← если база называется иначе, поправь (см. config_local.py)
DIR="/home/reform/backups"
KEEP_DAYS=14
OWNER="reform"

mkdir -p "$DIR"
STAMP="$(date +%F_%H%M)"
FILE="$DIR/${DB}_${STAMP}.sql.gz"

# Дамп от системного пользователя postgres (peer-аутентификация, пароль не нужен)
sudo -u postgres pg_dump "$DB" | gzip -9 > "$FILE"

# Свежая копия с фиксированным именем — её тянет компьютер
cp -f "$FILE" "$DIR/${DB}_latest.sql.gz"

# Ротация: удалить датированные дампы старше KEEP_DAYS (latest не трогаем)
find "$DIR" -name "${DB}_*.sql.gz" ! -name "${DB}_latest.sql.gz" -mtime +"$KEEP_DAYS" -delete

# Чтобы компьютер мог скачать файлы по ssh от пользователя reform
chown -R "$OWNER":"$OWNER" "$DIR"

echo "$(date '+%F %T') backup ok: $FILE ($(du -h "$FILE" | cut -f1))"
