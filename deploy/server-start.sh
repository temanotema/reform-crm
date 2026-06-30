#!/usr/bin/env bash
# Поднять Re.form (бот + панель) в АВТОНОМНОМ режиме:
# enable = автозапуск при загрузке сервера + авто-рестарт при сбоях,
# start  = запустить прямо сейчас.
# Работает само, пока не остановишь server-stop.sh.
sudo systemctl enable reform-bot reform-web
sudo systemctl start reform-bot reform-web
echo "✅ Re.form запущен (бот + панель). Работает автономно до server-stop.sh."
echo -n "reform-bot: "; systemctl is-active reform-bot
echo -n "reform-web: "; systemctl is-active reform-web
