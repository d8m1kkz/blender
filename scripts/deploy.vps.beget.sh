#!/usr/bin/env bash
# Деплой gen и caddy (compose.vps.yml) на VPS Beget: первый и повторный (обновление).
# Рассчитан на готовый сервер: Docker и docker compose уже установлены и запущены, вход по SSH под root.
# На сервере ничего не устанавливается: PHP и Composer там не нужны, всё работает в контейнерах.
# Сайт (php) живёт на виртуальном хостинге, его кладёт scripts/deploy.beget.sh.
# Перед этим образ gen должен быть на Docker Hub: scripts/prepare.sh gen
# Использование: scripts/deploy.vps.beget.sh [домен]   — без домена: <IP через дефисы>.sslip.io
set -euo pipefail
cd "$(dirname "$0")/.." # корень проекта: там compose-файлы

HOST=begetvps   # алиас из ~/.ssh/config
IP=155.212.190.33 # внешний адрес этого VPS (нужен только для домена по умолчанию)
SITE_HOST=${1:-${IP//./-}.sslip.io}

# Одним подключением: передать compose-файлы, записать SITE_HOST, скачать образы и запустить.
# .env нужен и для ручных команд docker compose на сервере: без SITE_HOST они не запускаются.
tar cf - compose.vps.yml compose_gen.yml |
	ssh "$HOST" "
	set -e
	mkdir -p app
	cd app
	tar xf - --no-same-owner
	echo 'SITE_HOST=$SITE_HOST' > .env
	docker compose -f compose.vps.yml pull
	docker compose -f compose.vps.yml up -d --no-build --remove-orphans
	docker image prune -f   # старые образы после обновления: экономит диск
"

# Ждём ответа по HTTPS: caddy должен получить сертификат, а Blender — запуститься
URL="https://$SITE_HOST"
echo "Жду $URL/limits …"
if ! curl -fs -o /dev/null -m 5 --retry 40 --retry-delay 3 --retry-all-errors "$URL/limits"; then
	echo "gen не ответил за отведённое время. Логи:" >&2
	echo "  ssh $HOST 'cd app && docker compose -f compose.vps.yml logs caddy gen'" >&2
	exit 1
fi
echo "Готово: $URL"
echo "Чтобы сайт ходил в этот gen: GEN_ORIGINS=$URL в php/.env.beget, затем scripts/deploy.beget.sh"
