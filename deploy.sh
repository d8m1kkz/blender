#!/usr/bin/env bash
set -euo pipefail

IP=${1:?"Использование: $0 <IP-адрес>"}
cd "$(dirname "$0")"
export COMPOSE_FILE=compose.prod.yml

# Домен для HTTPS: sslip.io сам резолвит «1-2-3-4.sslip.io» в 1.2.3.4, регистрировать ничего не нужно
SITE_HOST="${IP//./-}.sslip.io"

SSH=(ssh -i ~/.ssh/key.pem
	-o StrictHostKeyChecking=no     # сервер новый: fingerprint не спрашивать...
	-o UserKnownHostsFile=/dev/null # ...и не засорять known_hosts
	-o LogLevel=ERROR               # без «Warning: Permanently added…»
	-o ConnectionAttempts=30        # VPS может ещё загружаться
	-o ConnectTimeout=5
	ubuntu@"$IP")

# На VPS, одним подключением: распаковать файлы (compose-файл под именем
#    compose.yml), дождаться cloud-init, поставить Docker, записать SITE_HOST в .env
#    (его подхватывает compose), обновить образы, запустить, почистить
tar cf - --transform="s|^$COMPOSE_FILE\$|compose.yml|" "$COMPOSE_FILE" web/.env.production |
	"${SSH[@]}" "SITE_HOST=$SITE_HOST"'
	set -euo pipefail
	mkdir -p app
	tar xf - -C app
	cloud-init status --wait >/dev/null || [ $? -eq 2 ]
	command -v docker >/dev/null || curl -fsSL https://get.docker.com | sudo sh
	cd app
	echo "SITE_HOST=$SITE_HOST" > .env
	# по одному сервису: скачать, перезапустить, сразу удалить старый образ (экономит диск);
	# caddy последним: он забирает порты 80/443 и сразу идёт за сертификатом
	for s in gen mcp php nginx caddy; do
		sudo docker compose pull "$s"
		sudo docker compose up -d --no-build "$s"
		sudo docker image prune -f
	done
'

# Caddy получает сертификат Let's Encrypt при первом запуске (обычно 10–30 с): ждём, пока HTTPS заработает
echo "Жду HTTPS на https://$SITE_HOST (Caddy получает сертификат)…"
for _ in $(seq 40); do
	if curl -fsS -o /dev/null -m 5 "https://$SITE_HOST/" 2>/dev/null; then
		echo "Готово: https://$SITE_HOST"
		echo "MCP:    https://$SITE_HOST/mcp"
		exit 0
	fi
	sleep 3
done
echo "HTTPS не ответил за 2 минуты. Проверьте, что на VPS открыты порты 80 и 443, и логи:" >&2
echo "  ssh ubuntu@$IP 'cd app && sudo docker compose logs caddy'" >&2
exit 1
