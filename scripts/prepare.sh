#!/usr/bin/env bash
# Собрать образы и отправить их на Docker Hub (один раз перед этим: docker login).
# Использование: scripts/prepare.sh [сервис…]   — без аргументов все; scripts/prepare.sh gen — только gen (для Render)
set -euo pipefail

cd "$(dirname "$0")/.." # корень проекта: там compose-файлы
# -f compose.yml явно: без этого подхватился бы compose.override.yml (разработка)
docker compose -f compose.yml build --push "$@"
