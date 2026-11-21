#!/usr/bin/env bash
# check_resources.sh — сколько ресурсов осталось на VPS после установки приложений.
# Запуск: bash check_resources.sh   (лучше от root или через sudo — тогда видно все процессы и порты)

set -u  # ошибка при использовании необъявленной переменной

# ---------- запуск с вашего компьютера ----------
# Если передан IP первым параметром — отправляем этот же файл на сервер по SSH
# и выполняем там (через stdin, на сервере ничего не сохраняется), затем выходим.
#   Пример: bash check_resources.sh 203.0.113.10
if [ $# -ge 1 ]; then
  IP="$1"
fi
# Без параметров скрипт работает локально (когда выполняется уже на сервере).

# ---------- сохранение отчёта в Markdown ----------
# Весь вывод дублируется в файл с тем же именем и расширением .md
# (check_resources.md рядом со скриптом). Файл каждый раз перезаписывается.
# Условие -f "$0": при запуске через "ssh ... bash -s" имени файла нет, там не сохраняем.
if [ -z "${CHECK_RES_INNER:-}" ] && [ -f "$0" ]; then
  OUT="${0%.*}.md"
  TMP=$(mktemp)
  if [ -n "${IP:-}" ]; then
    # Режим «с компьютера»: отправляем этот же файл на сервер по SSH
    ssh -i ~/.ssh/key.pem ubuntu@"$IP" 'sudo bash -s' < "$0" 2>&1 | tee "$TMP"
    WHERE="сервер $IP"
  else
    # Режим «на самом сервере»: запускаем себя же, пометив, что сохранять не надо повторно
    CHECK_RES_INNER=1 bash "$0" 2>&1 | tee "$TMP"
    WHERE="$(hostname)"
  fi
  # Оборачиваем вывод в блок кода, чтобы Markdown не ломал таблицы и выравнивание
  { printf '# Отчёт о ресурсах: %s\n\nДата: %s\n\n```text\n' "$WHERE" "$(date '+%F %T')"
    cat "$TMP"
    printf '```\n'; } > "$OUT"
  rm -f "$TMP"
  echo "Отчёт сохранён: $OUT"
  exit 0
fi
# Сюда попадаем, только когда скрипт выполняется на сервере: дальше идёт сама проверка.

# ---------- вспомогательные функции ----------
hr()   { printf '\n==================== %s ====================\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }   # есть ли команда в системе

# ---------- 1. Процессор ----------
hr "ПРОЦЕССОР (CPU)"
CORES=$(nproc)
echo "Ядер: $CORES"

# Load average: средняя очередь задач за 1/5/15 минут.
# Ориентир: если значение стабильно выше числа ядер — процессор перегружен.
read -r L1 L5 L15 _ < /proc/loadavg
echo "Load average (1/5/15 мин): $L1 / $L5 / $L15  (ядер: $CORES)"

# Реальная загрузка CPU за 1 секунду: читаем /proc/stat дважды и сравниваем.
read -r _ u1 n1 s1 i1 w1 q1 sq1 st1 _ < /proc/stat
sleep 1
read -r _ u2 n2 s2 i2 w2 q2 sq2 st2 _ < /proc/stat
IDLE=$(( (i2 + w2) - (i1 + w1) ))
TOTAL=$(( (u2+n2+s2+i2+w2+q2+sq2+st2) - (u1+n1+s1+i1+w1+q1+sq1+st1) ))
if [ "$TOTAL" -gt 0 ]; then
  USED=$(( 100 * (TOTAL - IDLE) / TOTAL ))
  echo "Загрузка CPU сейчас: ${USED}%   Свободно: $((100 - USED))%"
fi
# steal (st) — время, которое у вашей VM «украл» соседний клиент хостера.
# Если стабильно > 5–10%, VPS перепродан, и это не ваша вина.
echo "Steal за секунду: $(( st2 - st1 )) тиков (стабильно большое значение = перегруженный хост)"

# ---------- 2. Оперативная память ----------
hr "ПАМЯТЬ (RAM) И SWAP"
# Колонка "available" — главная: сколько реально можно отдать новым программам
# (с учётом кэша, который ядро освободит при необходимости). "free" смотреть не надо.
free -h
MEM_TOTAL=$(awk '/MemTotal/{print $2}' /proc/meminfo)
MEM_AVAIL=$(awk '/MemAvailable/{print $2}' /proc/meminfo)
echo "Доступно памяти: $(( 100 * MEM_AVAIL / MEM_TOTAL ))% от общего объёма"
SWAP_USED=$(awk '/SwapTotal/{t=$2} /SwapFree/{f=$2} END{print t-f}' /proc/meminfo)
if [ "$SWAP_USED" -gt 0 ]; then
  echo "ВНИМАНИЕ: используется swap ($((SWAP_USED/1024)) МБ) — памяти уже не хватает или была нехватка."
fi
# Если OOM-killer убивал процессы — это прямой признак нехватки памяти.
if have journalctl; then
  OOM=$(journalctl -k --no-pager 2>/dev/null | grep -ci 'out of memory\|oom-kill')
  echo "Срабатываний OOM-killer в журнале ядра: $OOM"
fi

# ---------- 3. Диск ----------
hr "ДИСК"
# Только реальные файловые системы (без tmpfs, overlay и т.п.)
df -hT -x tmpfs -x devtmpfs -x squashfs -x overlay
echo
echo "Inodes (если закончатся, файлы не создать даже при свободном месте):"
df -i -x tmpfs -x devtmpfs -x squashfs -x overlay | awk 'NR==1 || $1 ~ /^\//'

# Что занимает место: верхний уровень корня (без других ФС, -x)
echo
echo "Самые большие каталоги в / (топ 8):"
du -xh --max-depth=1 / 2>/dev/null | sort -rh | head -9

# ---------- 4. Docker (если установлен) ----------
if have docker; then
  hr "DOCKER"
  # Потребление ресурсов контейнерами прямо сейчас (один снимок, без потока)
  docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}' 2>/dev/null
  echo
  # Сколько места занимают образы, контейнеры, тома и кэш сборки
  docker system df 2>/dev/null
  echo "Освободить место: docker system prune -a  (удалит ВСЕ неиспользуемые образы — убедитесь, что они не нужны)"
fi

# ---------- 5. Кто больше всего ест ----------
hr "ТОП-5 ПРОЦЕССОВ ПО ПАМЯТИ"
ps -eo pid,user,%mem,rss,comm --sort=-%mem | head -6

hr "ТОП-5 ПРОЦЕССОВ ПО CPU"
ps -eo pid,user,%cpu,comm --sort=-%cpu | head -6

# ---------- 6. Сеть и службы ----------
hr "ОТКРЫТЫЕ ПОРТЫ (слушающие)"
# Проверка, что поднялось только нужное и ничего лишнего не торчит наружу
ss -tulnp 2>/dev/null | awk 'NR==1 || /LISTEN|UNCONN/'

hr "УПАВШИЕ СЛУЖБЫ systemd"
if have systemctl; then
  systemctl --failed --no-pager --no-legend | sed 's/^/  /' | { grep . || echo "  Нет, всё в порядке."; }
fi

# ---------- 7. Итоговое заключение ----------
hr "ИТОГ"
ROOT_USE=$(df / --output=pcent | tail -1 | tr -dc '0-9')
MEM_FREE_PCT=$(( 100 * MEM_AVAIL / MEM_TOTAL ))
warn=0
[ "$ROOT_USE" -ge 85 ]      && { echo "⚠ Диск / заполнен на ${ROOT_USE}%";              warn=1; }
[ "$MEM_FREE_PCT" -le 15 ]  && { echo "⚠ Доступной памяти всего ${MEM_FREE_PCT}%";      warn=1; }
[ "$SWAP_USED" -gt 0 ]      && { echo "⚠ Задействован swap";                            warn=1; }
# сравнение load average с числом ядер (через awk, т.к. число дробное)
awk -v l="$L5" -v c="$CORES" 'BEGIN{exit !(l>c)}' && { echo "⚠ Load average (5 мин) $L5 выше числа ядер $CORES"; warn=1; }
[ "$warn" -eq 0 ] && echo "✔ Запас ресурсов в норме."