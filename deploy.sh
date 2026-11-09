#!/usr/bin/env bash

set -euo pipefail

if [ -z "${1:-}" ]; then
    echo "Ожитается вызов в формате: $0 <IP-адрес>" >&2
    exit 1
fi

SSH_OPTS=(
	-i ~/.ssh/key.pem
	-o StrictHostKeyChecking=no     # Не запрашивать ручное подтверждение fingerprint
    -o UserKnownHostsFile=/dev/null # Не сохранять временный ключ в known_hosts
    -o LogLevel=ERROR               # Скрыть информационные сообщения
)

ssh "${SSH_OPTS[@]}" "ubuntu@$1" 'bash -s' << 'EOF'
set -euo pipefail
	
cloud-init status --wait # Ожидание завершения фоновых скриптов облака

# sudo apt update # Обновление локальных списков пакетов

sudo apt clean
EOF