#!/usr/bin/env bash
# Раз в 5 секунд спрашивает у развёрнутого gen наименьший запас памяти (GET /memory)
# и печатает строку только когда она изменилась, то есть когда пик вырос.
# Использование: scripts/check.poll_mem.sh [адрес gen] [период в секундах]
# Пример:        scripts/check.poll_mem.sh https://blender-gen-render.onrender.com 5
# Остановка: Ctrl+C.
set -u

URL="${1:-https://blender-gen-render.onrender.com}"
PERIOD="${2:-5}"
prev=""

echo "Опрашиваю $URL/memory каждые ${PERIOD} с (печатаю только изменения)"
while true; do
	# Первый запрос после простоя может идти около минуты: сервис «просыпается»
	cur=$(curl -s -m 90 "$URL/memory") || cur=""
	if [ -z "$cur" ]; then
		echo "$(date +%H:%M:%S) нет ответа"
	elif [ "$cur" != "$prev" ]; then
		echo "$(date +%H:%M:%S) $cur"
		prev="$cur"
	fi
	sleep "$PERIOD"
done
