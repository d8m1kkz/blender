#!/usr/bin/env bash
# Деплой gen и caddy (compose.vps.yml) на VPS Beget: первый и повторный (обновление).
# Запускается и вручную на рабочем компьютере, и из GitHub Actions (.github/workflows/deploy-gen.yml).
# Рассчитан на готовый сервер: Docker и docker compose уже установлены и запущены, вход по SSH под root.
# На сервере ничего не устанавливается: PHP и Composer там не нужны, всё работает в контейнерах.
# Сайт (php) живёт на виртуальном хостинге, его кладёт scripts/deploy.beget.sh.
# Образ gen берётся с Docker Hub: его собирает и отправляет туда тот же сценарий GitHub Actions.
# Использование: scripts/deploy.vps.beget.sh [домен]   — без домена: <IP через дефисы>.sslip.io
# Вход по SSH: свой ключ берётся из настроек SSH — на рабочем компьютере это блок begetvps
# в ~/.ssh/config, на GitHub — ssh-agent (ключ в него кладёт deploy-gen.yml).
# Ключ самого VPS заранее не проверяется (accept-new, см. .claude/CLAUDE.md). Если после переустановки
# VPS ssh отказывается подключаться: ssh-keygen -R 155.212.190.33
set -euo pipefail
cd "$(dirname "$0")/.." # корень проекта: там compose-файлы

IP=155.212.190.33 # внешний адрес этого VPS
SITE_HOST=${1:-${IP//./-}.sslip.io}
URL="https://$SITE_HOST"

# Сообщить об ошибке и выйти. На GitHub сообщение дополнительно видно на странице запуска
fail() {
	[ -z "${GITHUB_ACTIONS:-}" ] || echo "::error::$*"
	echo "Ошибка: $*" >&2
	exit 1
}

# Адрес и пользователь заданы здесь, поэтому настройки SSH для входа не обязательны.
# begetvps — имя блока в ~/.ssh/config, откуда берётся ключ на рабочем компьютере.
# accept-new: незнакомый сервер принимается без вопроса, сервер со сменившимся ключом отклоняется
SSH=(ssh -o HostName="$IP" -o User=root -o StrictHostKeyChecking=accept-new)

echo "== Деплой на $IP"
# Одним подключением: передать compose-файлы, записать SITE_HOST, скачать образы и запустить.
# .env нужен и для ручных команд docker compose на сервере: без SITE_HOST они не запускаются.
tar cf - compose.vps.yml compose_gen.yml |
	"${SSH[@]}" begetvps "
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
echo "== Жду $URL/limits …"
curl -fs -o /dev/null -m 5 --retry 40 --retry-delay 3 --retry-all-errors "$URL/limits" ||
	fail "gen не ответил за отведённое время. Логи: ssh begetvps 'cd app && docker compose -f compose.vps.yml logs caddy gen'"

echo "Готово: $URL"
echo "Чтобы сайт ходил в этот gen: GEN_ORIGINS=$URL в php/.env.beget, затем scripts/deploy.beget.sh"
