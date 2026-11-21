#!/usr/bin/env bash
set -euo pipefail

IP=${1:?"Использование: $0 <IP-адрес>"}
cd "$(dirname "$0")"
export COMPOSE_FILE=compose.prod.yml

SSH=(ssh -i ~/.ssh/key.pem
	-o StrictHostKeyChecking=no     # сервер новый: fingerprint не спрашивать...
	-o UserKnownHostsFile=/dev/null # ...и не засорять known_hosts
	-o LogLevel=ERROR               # без «Warning: Permanently added…»
	-o ConnectionAttempts=30        # VPS может ещё загружаться
	-o ConnectTimeout=5
	ubuntu@"$IP")

# На VPS, одним подключением: распаковать файлы (compose-файл под именем
#    compose.yml), дождаться cloud-init, поставить Docker, обновить образы, запустить, почистить
tar cf - --transform="s|^$COMPOSE_FILE\$|compose.yml|" "$COMPOSE_FILE" web/.env.production |
	"${SSH[@]}" '
	set -euo pipefail
	mkdir -p app
	tar xf - -C app
	cloud-init status --wait >/dev/null || [ $? -eq 2 ]
	command -v docker >/dev/null || curl -fsSL https://get.docker.com | sudo sh
	cd app
	# по одному сервису: скачать, перезапустить, сразу удалить старый образ (экономит диск)
	for s in gen mcp php nginx; do
		sudo docker compose pull "$s"
		sudo docker compose up -d --no-build "$s"
		sudo docker image prune -f
	done
'