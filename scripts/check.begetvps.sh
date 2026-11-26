#!/usr/bin/env bash
# Проверка VPS по SSH: что установлено и что запущено.
# Запускается на твоём компьютере, сам подключается к серверу.
# Только читает информацию, ничего не меняет на сервере.
# Запуск: scripts/check.begetvps.sh
# Отчёт также сохраняется в vps-report.txt рядом со скриптом.

HOST="begetvps"   # алиас из ~/.ssh/config, то же, что команда: ssh begetvps
cd "$(dirname "$0")" || exit 1 # чтобы отчёт лёг рядом со скриптом, откуда бы его ни запустили

ssh "$HOST" 'bash -s' <<'REMOTE' | tee vps-report.txt

section() { printf '\n=== %s ===\n' "$1"; }

# Показывает версию команды или "НЕ УСТАНОВЛЕНО"
check() {
  local name="$1"; shift
  if command -v "$name" >/dev/null 2>&1; then
    printf '%-18s %s\n' "$name" "$("$@" 2>&1 | head -n1)"
  else
    printf '%-18s %s\n' "$name" "НЕ УСТАНОВЛЕНО"
  fi
}

section "Система"
. /etc/os-release 2>/dev/null && echo "ОС: $PRETTY_NAME"
echo "Ядро: $(uname -r)"
echo "Архитектура: $(uname -m)"
echo "Виртуализация: $(systemd-detect-virt 2>/dev/null || echo неизвестно)"
echo "Хост: $(hostname)"
echo "Аптайм: $(uptime -p 2>/dev/null)"
echo "Пользователь: $(whoami)"

section "Ресурсы"
echo "CPU ядер: $(nproc)"
free -h | sed -n '1,3p'
df -h /

section "Сеть"
echo "Внутренние адреса: $(hostname -I 2>/dev/null)"
echo "Внешний IP: $(curl -s --max-time 5 https://ifconfig.me 2>/dev/null || echo недоступно)"

section "Инструменты разработки"
check git git --version
check curl curl --version
check wget wget --version
check python3 python3 --version
check pip3 pip3 --version
check node node --version
check npm npm --version
check php php --version
check composer composer --version
check nginx nginx -v
check make make --version
check unzip unzip -v
check htop htop --version
check ufw ufw version
check fail2ban-client fail2ban-client --version

section "Docker"
check docker docker --version
if docker compose version >/dev/null 2>&1; then
  printf '%-18s %s\n' "docker compose" "$(docker compose version)"
else
  printf '%-18s %s\n' "docker compose" "НЕ УСТАНОВЛЕНО"
fi
check docker-compose docker-compose --version
if command -v docker >/dev/null 2>&1; then
  echo "Служба docker: $(systemctl is-active docker 2>/dev/null)"
  echo "Автозапуск docker: $(systemctl is-enabled docker 2>/dev/null)"
  echo "--- Контейнеры (все) ---"
  docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' 2>&1
  echo "--- Образы ---"
  docker images 2>&1
fi

section "Слушающие порты"
(ss -tulpn 2>/dev/null || netstat -tulpn 2>/dev/null) | head -n 30

section "Брандмауэр"
if command -v ufw >/dev/null 2>&1; then
  ufw status 2>&1
else
  echo "ufw не установлен"
fi

section "Запущенные службы"
systemctl list-units --type=service --state=running --no-pager --no-legend 2>/dev/null \
  | awk '{print $1}'

section "Вручную установленные пакеты (первые 60)"
apt-mark showmanual 2>/dev/null | head -n 60

section "Каталоги /opt и /opt/beget"
ls -la /opt 2>/dev/null
ls -la /opt/beget 2>/dev/null

section "Обновления"
if command -v apt >/dev/null 2>&1; then
  echo "Доступно обновлений: $(apt list --upgradable 2>/dev/null | tail -n +2 | wc -l)"
fi
[ -f /var/run/reboot-required ] && echo "Нужна перезагрузка" || echo "Перезагрузка не требуется"

echo
echo "Готово."
REMOTE
