#!/usr/bin/env bash
set -euo pipefail

IP=${1:?"Использование: $0 <IP-адрес>"}
cd "$(dirname "$0")"

SSH=(ssh -i ~/.ssh/key.pem
	-o StrictHostKeyChecking=no     # сервер новый: fingerprint не спрашивать...
	-o UserKnownHostsFile=/dev/null # ...и не засорять known_hosts
	-o LogLevel=ERROR               # без «Warning: Permanently added…»
	-o ConnectionAttempts=30        # VPS может ещё загружаться
	-o ConnectTimeout=5
	ubuntu@"$IP")

# На VPS, одним подключением: распаковать файлы (compose.yml — это и есть production;
#    compose.override.yml, файл для разработки, на VPS не едет), дождаться cloud-init,
#    поставить Docker, обновить образы, запустить, почистить
tar cf - compose.yml .env php/.env.beget |
	"${SSH[@]}" '
	set -euo pipefail
	mkdir -p app
	tar xf - -C app
	cloud-init status --wait >/dev/null || [ $? -eq 2 ]
	command -v docker >/dev/null || curl -fsSL https://get.docker.com | sudo sh
	cd app
	# по одному сервису: скачать, перезапустить, сразу удалить старый образ (экономит диск);
	# nginx последним: он занимает порт 80, а ему нужен php. Перед его запуском --remove-orphans
	# удаляет контейнеры сервисов, которых больше нет в compose.yml (прежний web держал бы порт 80)
	for s in gen php nginx; do
		opts=()
		if [ "$s" = nginx ]; then opts=(--remove-orphans); fi
		sudo docker compose pull "$s"
		sudo docker compose up -d --no-build "${opts[@]}" "$s"
		sudo docker image prune -f
	done
'

# Ждём, пока сайт ответит по HTTP (порт 80 на VPS должен быть открыт)
echo "Жду http://$IP …"
for _ in $(seq 40); do
	if curl -fsS -o /dev/null -m 5 "http://$IP/" 2>/dev/null; then
		echo "Готово: http://$IP"
		exit 0
	fi
	sleep 3
done
echo "Сайт не ответил за 2 минуты. Проверьте, что на VPS открыт порт 80, и логи:" >&2
echo "  ssh ubuntu@$IP 'cd app && sudo docker compose logs nginx php'" >&2
exit 1
