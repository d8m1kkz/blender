#!/usr/bin/env bash
# Замер оперативной памяти gen под лимитом 512 МБ (как на бесплатном Render).
# Использование: scripts/measure_mem.sh [лимит, по умолчанию 512m] [образ, по умолчанию d8m1kkz/blender-gen:latest]
# Перед этим образ нужно собрать: docker compose -f compose.yml build gen
set -euo pipefail
cd "$(dirname "$0")/.."

LIMIT="${1:-512m}"
IMAGE="${2:-d8m1kkz/blender-gen:latest}"
NAME=gen-memtest

docker rm -f "$NAME" >/dev/null 2>&1 || true
# --memory-swap = --memory: запрещаем своп, иначе контейнер «выживет» за счёт диска, а на Render его убьют.
# PORT как на Render. /dev/shm — по умолчанию Docker даёт 64 МБ; ставим 256 МБ, как в compose.
docker run -d --name "$NAME" --memory="$LIMIT" --memory-swap="$LIMIT" --shm-size=256m \
	-e PORT=10000 -e PYTHONUNBUFFERED=1 "$IMAGE" >/dev/null
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT

docker cp scripts/memtest.py "$NAME":/app/memtest.py
echo "Контейнер запущен с лимитом $LIMIT, жду прогрев Blender и замеряю…"
docker exec -e PORT=10000 "$NAME" python /app/memtest.py || {
	echo "Замер прервался. Состояние контейнера:"
	docker inspect -f 'OOMKilled={{.State.OOMKilled}} ExitCode={{.State.ExitCode}}' "$NAME"
	docker logs --tail 20 "$NAME"
	exit 1
}
