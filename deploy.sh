#!/bin/bash
# Обновляет бота на сервере до последней версии из GitHub.
#
# Порядок:  git push   →   ./deploy.sh
#
# На сервере: git pull, установка библиотек из requirements.txt, перезапуск, проверка логов.
set -e
ssh travel-bot 'set -e
cd /opt/english-bot
as_bot() { runuser -u englishbot -- env HOME=/opt/english-bot "$@"; }

echo "▶ git pull"
as_bot git pull --ff-only
echo "▶ библиотеки"
as_bot .venv/bin/pip install -q --no-cache-dir -r requirements.txt
echo "▶ перезапуск"
systemctl restart english-bot
sleep 10

echo "english-bot: $(systemctl is-active english-bot)   версия: $(as_bot git log --oneline -1)"
journalctl -u english-bot --since "-15 sec" --no-pager | grep -E "Run polling|ERROR|Traceback" || true
echo "travel-bot:  $(systemctl is-active travel-bot)"'
