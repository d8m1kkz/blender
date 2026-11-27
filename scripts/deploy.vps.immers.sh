#!/usr/bin/env bash
# Деплой gen и caddy (compose.vps.yml) на VPS, идемпотентный.
# Образ gen берётся с Docker Hub: туда его отправляет GitHub Actions (.github/workflows/deploy-gen.yml).
# Если VPS не может скачать образ, он передаётся напрямую с этой машины (docker save | docker load).
# Использование: scripts/deploy.vps.immers.sh [IP-адрес] [домен]
set -euo pipefail

IP=${1:-81.94.159.64}
SITE_HOST=${2:-${IP//./-}.sslip.io} # без домена: 1.2.3.4 → 1-2-3-4.sslip.io
SSH_USER=${SSH_USER:-ubuntu}
cd "$(dirname "$0")/.." # корень проекта: там compose-файлы

SSH=(ssh -i ~/.ssh/key.pem
	-o StrictHostKeyChecking=no     # сервер новый: fingerprint не спрашивать...
	-o UserKnownHostsFile=/dev/null # ...и не засорять known_hosts
	-o LogLevel=ERROR               # без «Warning: Permanently added…»
	-o ConnectionAttempts=30        # VPS может ещё загружаться
	-o ConnectTimeout=5
	"$SSH_USER@$IP")

# На VPS, одним подключением: распаковать файлы (compose.vps.yml и подключаемый им compose_gen.yml),
#    дождаться cloud-init, поставить Docker, записать SITE_HOST в .env
tar cf - compose.vps.yml compose_gen.yml |
	"${SSH[@]}" "SITE_HOST='$SITE_HOST'"'
	set -euo pipefail
	mkdir -p app
	tar xf - -C app
	cloud-init status --wait >/dev/null || [ $? -eq 2 ]
	command -v docker >/dev/null || curl -fsSL https://get.docker.com | sudo sh
	# .env нужен и для ручных команд docker compose на сервере: без SITE_HOST они не запускаются
	echo "SITE_HOST=$SITE_HOST" > app/.env
'

# По одному сервису: получить образ, перезапустить, сразу удалить старый образ (экономит диск).
# Имена образов те же, что в compose_gen.yml и compose.vps.yml.
# --remove-orphans удаляет контейнеры сервисов, которых нет в compose.vps.yml
# (прежние nginx и php; nginx держал бы порт 80, нужный caddy)
COMPOSE="cd app && sudo docker compose -f compose.vps.yml"
for pair in gen=d8m1kkz/blender-gen:latest caddy=caddy:2; do
	service=${pair%%=*}
	image=${pair#*=}
	if ! "${SSH[@]}" "$COMPOSE pull $service"; then
		# уйдёт локальная версия образа; нет её на этой машине — скрипт остановится
		echo "VPS не скачал $image. Передаю образ с этой машины (может занять несколько минут)…" >&2
		docker save "$image" | "${SSH[@]}" 'sudo docker load'
	fi
	"${SSH[@]}" "$COMPOSE up -d --no-build --remove-orphans $service && sudo docker image prune -f"
done

# Ждём, пока gen ответит по HTTPS: caddy должен получить сертификат, а Blender — запуститься
# (порты 80 и 443 на VPS должны быть открыты)
URL="https://$SITE_HOST"
echo "Жду $URL/limits …"
for _ in $(seq 40); do
	if curl -fsS -o /dev/null -m 5 "$URL/limits" 2>/dev/null; then
		echo "Готово: $URL"
		echo "Чтобы сайт ходил в этот gen: GEN_ORIGINS=$URL в php/.env.beget, затем scripts/deploy.beget.sh"
		exit 0
	fi
	sleep 3
done
echo "gen не ответил за отведённое время. Проверьте, что на VPS открыты порты 80 и 443, и логи:" >&2
echo "  ssh -i ~/.ssh/key.pem $SSH_USER@$IP 'cd app && sudo docker compose -f compose.vps.yml logs caddy gen'" >&2
exit 1
