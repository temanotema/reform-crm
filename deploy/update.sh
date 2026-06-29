#!/usr/bin/env bash
# update.sh — деплой одной командой на сервере.
# Подтягивает свежий код из GitHub, обновляет зависимости, перезапускает сервисы.
# Запуск:  bash ~/reform/deploy/update.sh
set -e

cd /home/reform/reform

echo "→ Забираю свежий код (git pull)"
git pull --ff-only

echo "→ Обновляю зависимости"
venv/bin/pip install -q -r requirements.txt

echo "→ Перезапускаю сервисы"
sudo systemctl restart reform-bot reform-web

echo "✓ Готово. Текущий статус:"
systemctl --no-pager --lines=0 status reform-bot reform-web || true
