#!/usr/bin/env bash
# Собрать образы и отправить их на Docker Hub (один раз перед этим: docker login).
# Использование: scripts/prepare.sh [сервис…]   — без аргументов все; scripts/prepare.sh gen — только gen (для Render)
set -euo pipefail

cd "$(dirname "$0")/.." # корень проекта: там compose-файлы

# Отметка в образе gen: версия папки gen в git (см. compose_gen.yml).
# При незакоммиченных изменениях пустая: образ не соответствует ни одной версии, и деплой из GitHub его не примет
GEN_TREE=
[ -n "$(git status --porcelain -- gen)" ] || GEN_TREE=$(git rev-parse HEAD:gen)
export GEN_TREE

# -f compose.yml явно: без этого подхватился бы compose.override.yml (разработка)
docker compose -f compose.yml build --push "$@"
