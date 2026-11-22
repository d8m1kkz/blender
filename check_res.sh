#!/usr/bin/env bash
# check_res.sh — сколько ресурсов осталось на VPS после установки приложений.
#
# Запуск на самом сервере:   bash check_res.sh [--load [сек]]   (лучше через sudo)
# Запуск с вашего компьютера: bash check_res.sh <IP> [--load [сек]]
#   --load [сек]  — дополнительно замерить ПИКИ CPU/RAM контейнеров за N секунд (по умолчанию 60).
#                   Во время замера двигайте слайдеры в браузере или вызывайте set_basket:
#                   без нагрузки скрипт видит только простой, а Blender нагружает сервер всплесками.
# Переменные: SSH_KEY (по умолчанию ~/.ssh/key.pem), SSH_USER (ubuntu), SITE_HOST (для проверки HTTPS;
#             при запуске с IP считается сам: <IP через дефисы>.sslip.io).

set -u          # ошибка при использовании необъявленной переменной
set -o pipefail # код возврата конвейера — первая ошибка (иначе ошибку ssh скроет tee)

SSH_KEY="${SSH_KEY:-$HOME/.ssh/key.pem}"
SSH_USER="${SSH_USER:-ubuntu}"
LOAD_SECS="${LOAD_SECS:-0}"
SITE_HOST="${SITE_HOST:-}"
IP=""

# ---------- разбор параметров ----------
while [ $# -gt 0 ]; do
  case "$1" in
    --load)
      LOAD_SECS=60
      # число после --load необязательно
      if [ $# -gt 1 ] && [[ "$2" =~ ^[0-9]+$ ]]; then LOAD_SECS="$2"; shift; fi ;;
    -*) echo "Неизвестный параметр: $1" >&2; exit 2 ;;
    *)  IP="$1" ;;
  esac
  shift
done
[[ "$LOAD_SECS" =~ ^[0-9]+$ ]] || { echo "LOAD_SECS должно быть числом" >&2; exit 2; }

# ---------- запуск с компьютера и сохранение отчёта в Markdown ----------
# Весь вывод дублируется в <имя скрипта>.md рядом со скриптом (перезаписывается).
# На сервере (CHECK_RES_INNER=1) эту часть пропускаем: сразу идёт сама проверка.
if [ -z "${CHECK_RES_INNER:-}" ] && [ -f "$0" ]; then
  OUT="${0%.*}.md"
  TMP=$(mktemp)
  if [ -n "$IP" ]; then
    # Режим «с компьютера»: отправляем этот же файл на сервер по SSH (через stdin, на сервере файла нет)
    [ -z "$SITE_HOST" ] && SITE_HOST="${IP//./-}.sslip.io"
    ssh -i "$SSH_KEY" -o ConnectTimeout=10 -o BatchMode=yes \
      -o StrictHostKeyChecking=accept-new "$SSH_USER@$IP" \
      "sudo CHECK_RES_INNER=1 LOAD_SECS=$LOAD_SECS SITE_HOST=$SITE_HOST bash -s" < "$0" 2>&1 | tee "$TMP"
    RC=${PIPESTATUS[0]}
    WHERE="сервер $IP"
  else
    # Режим «на самом сервере»: запускаем себя же, пометив, что сохранять повторно не надо
    CHECK_RES_INNER=1 LOAD_SECS=$LOAD_SECS SITE_HOST=$SITE_HOST bash "$0" 2>&1 | tee "$TMP"
    RC=${PIPESTATUS[0]}
    WHERE="$(hostname)"
  fi
  if [ "$RC" -ne 0 ]; then
    echo "Проверка завершилась с ошибкой (код $RC), отчёт не сохранён." >&2
    rm -f "$TMP"
    exit "$RC"
  fi
  # Оборачиваем вывод в блок кода, чтобы Markdown не ломал таблицы и выравнивание
  { printf '# Отчёт о ресурсах: %s\n\nДата: %s\n\n```text\n' "$WHERE" "$(date '+%F %T')"
    cat "$TMP"
    printf '```\n'; } > "$OUT"
  rm -f "$TMP"
  echo "Отчёт сохранён: $OUT"
  exit 0
fi

# ---------- вспомогательные функции ----------
hr()   { printf '\n==================== %s ====================\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }   # есть ли команда в системе

warn=0
DOCKER_BAD=0
PEAK_CPU_PCT=0
PEAK_MEM_MIB=0

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
STEAL_PCT=0
if [ "$TOTAL" -gt 0 ]; then
  USED=$(( 100 * (TOTAL - IDLE) / TOTAL ))
  echo "Загрузка CPU сейчас: ${USED}%   Свободно: $((100 - USED))%"
  # steal — время, которое у вашей VM «украл» соседний клиент хостера.
  # Если стабильно > 5–10%, VPS перепродан, и это не ваша вина.
  STEAL_PCT=$(( 100 * (st2 - st1) / TOTAL ))
  echo "Steal: ${STEAL_PCT}% (стабильно >5–10% = перегруженный хост; замер всего за 1 с)"
fi

# ---------- 2. Оперативная память ----------
hr "ПАМЯТЬ (RAM) И SWAP"
# Колонка "available" — главная: сколько реально можно отдать новым программам
# (с учётом кэша, который ядро освободит при необходимости). "free" смотреть не надо.
free -h
MEM_TOTAL=$(awk '/MemTotal/{print $2}' /proc/meminfo)
MEM_AVAIL=$(awk '/MemAvailable/{print $2}' /proc/meminfo)
echo "Доступно памяти: $(( 100 * MEM_AVAIL / MEM_TOTAL ))% от общего объёма"
SWAP_TOTAL=$(awk '/SwapTotal/{print $2}' /proc/meminfo)
SWAP_USED=$(awk '/SwapTotal/{t=$2} /SwapFree/{f=$2} END{print t-f}' /proc/meminfo)
echo "Swap всего: $((SWAP_TOTAL/1024)) МБ, занято: $((SWAP_USED/1024)) МБ"
if [ "$SWAP_USED" -gt 0 ]; then
  echo "ВНИМАНИЕ: используется swap — памяти уже не хватает или была нехватка."
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
  hr "DOCKER: КОНТЕЙНЕРЫ"
  # -a: видны и остановленные; при restart: unless-stopped упавший контейнер уже перезапущен,
  # поэтому смотрим ещё RestartCount и OOMKilled — это точнее, чем журнал ядра.
  docker ps -a --format 'table {{.Names}}\t{{.Status}}' 2>/dev/null
  echo
  echo "Перезапуски и убийства по памяти (OOMKilled):"
  RESTARTS=$(for c in $(docker ps -aq 2>/dev/null); do
    docker inspect -f '{{.Name}} перезапусков={{.RestartCount}} oom={{.State.OOMKilled}}' "$c" 2>/dev/null
  done | sed 's/^\//  /')
  echo "$RESTARTS"
  echo "$RESTARTS" | grep -qE 'перезапусков=[1-9]|oom=true' && DOCKER_BAD=1

  hr "DOCKER: ПОТРЕБЛЕНИЕ СЕЙЧАС (простой)"
  docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}' 2>/dev/null
  echo
  # /dev/shm внутри gen: туда Blender пишет GLB; скрыт от df выше (-x tmpfs), а лимит — shm_size
  GEN=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -E '(^|[-_])gen([-_][0-9]+)?$' | head -1)
  if [ -n "$GEN" ]; then
    echo "/dev/shm в контейнере $GEN:"
    docker exec "$GEN" df -h /dev/shm 2>/dev/null | sed 's/^/  /'
  else
    echo "Контейнер gen не найден — /dev/shm не проверен."
  fi
  echo
  # Образы и диск: при деплое pull идёт ДО prune, поэтому на время обновления нужно место на два набора образов
  docker system df 2>/dev/null
  echo
  docker images --format 'table {{.Repository}}:{{.Tag}}\t{{.Size}}' 2>/dev/null
  IMG_MB=$(docker images --format '{{.Size}}' 2>/dev/null | awk '
    { n=$1+0; u=$1; gsub(/[0-9.]/,"",u)
      if (u ~ /^[Gg]/) n*=1000; else if (u ~ /^[Kk]/) n/=1000; else if (u !~ /^[Mm]/) n/=1000000
      s+=n } END{printf "%d", s}')
  FREE_MB=$(df -BM --output=avail / | tail -1 | tr -dc '0-9')
  echo "Образы: ~${IMG_MB} МБ, свободно на /: ${FREE_MB} МБ"
  echo "Освободить место: docker system prune -a  (удалит ВСЕ неиспользуемые образы — убедитесь, что они не нужны)"

  # ---------- 4b. Замер пиков под нагрузкой ----------
  if [ "$LOAD_SECS" -gt 0 ]; then
    hr "DOCKER: ПИКИ ЗА ${LOAD_SECS} с (под нагрузкой)"
    echo ">>> СЕЙЧАС двигайте слайдеры в браузере или вызывайте set_basket через MCP <<<"
    SAMPLES=$(mktemp)
    END=$(( SECONDS + LOAD_SECS ))
    while [ "$SECONDS" -lt "$END" ]; do
      # сам docker stats --no-stream занимает 1–2 с, отдельный sleep не нужен
      docker stats --no-stream --format '{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}' >> "$SAMPLES" 2>/dev/null
      echo '---' >> "$SAMPLES"
    done
    # Пик по каждому контейнеру и пик суммы по всем в один момент (разделитель выборки — строка ---).
    # CPU: 100% = одно ядро, у многопоточных процессов бывает больше.
    AWK_OUT=$(awk -F'|' '
      function mib(s,   n, u) {
        n = s + 0; u = s; gsub(/[0-9.]/, "", u)
        if (u ~ /^[Gg]/) return n * 1024
        if (u ~ /^[Mm]/) return n
        if (u ~ /^[Kk]/) return n / 1024
        return n / 1048576
      }
      $0 == "---" { if (st > pst) pst = st; if (sm > psm) psm = sm; st = 0; sm = 0; next }
      { c = $2; sub(/%/, "", c); c += 0
        split($3, p, " / "); m = mib(p[1])
        if (c > pc[$1]) pc[$1] = c
        if (m > pm[$1]) pm[$1] = m
        st += c; sm += m }
      END {
        for (k in pm) printf "  %-30s CPU пик %6.1f%%   RAM пик %8.1f МиБ\n", k, pc[k], pm[k]
        printf "TOTAL_PEAK %.0f %.0f\n", pst, psm
      }' "$SAMPLES")
    rm -f "$SAMPLES"
    printf '%s\n' "$AWK_OUT" | grep -v '^TOTAL_PEAK'
    read -r _ PEAK_CPU_PCT PEAK_MEM_MIB < <(printf '%s\n' "$AWK_OUT" | grep '^TOTAL_PEAK')
    echo "Сумма пиков всех контейнеров: CPU ${PEAK_CPU_PCT}% (ядер: $CORES), RAM ${PEAK_MEM_MIB} МиБ из $((MEM_TOTAL/1024)) МиБ"
  fi
fi

# ---------- 5. Кто больше всего ест ----------
hr "ТОП-5 ПРОЦЕССОВ ПО ПАМЯТИ"
ps -eo pid,user,%mem,rss,comm --sort=-%mem | head -6

hr "ТОП-5 ПРОЦЕССОВ ПО CPU (сейчас)"
# ps %cpu — среднее за всё время жизни процесса, поэтому берём второй кадр top (дельта за 1 с)
if have top; then
  top -bn2 -d1 -o %CPU 2>/dev/null | awk '/^top -/{n++} n==2' | sed -n '7,12p'
else
  ps -eo pid,user,%cpu,comm --sort=-%cpu | head -6
fi

# ---------- 6. Сеть и службы ----------
hr "ОТКРЫТЫЕ ПОРТЫ (слушающие)"
ss -tulnp 2>/dev/null
# Наружу ожидаются только 22/80/443; Docker публикует порты в обход ufw, поэтому смотрим вручную.
ODD_PORTS=$(ss -tuln 2>/dev/null | awk 'NR>1 {
    a = $5; if (a ~ /^127\./ || a ~ /^\[?::1\]?:/) next
    sub(/.*:/, "", a)
    if ($1 == "udp" && a == 68) next   # DHCP-клиент: порт слушает только ради ответа от DHCP-сервера
    if (a !~ /^(22|80|443)$/ && !(a in seen)) { seen[a] = 1; printf "%s ", a } }')
if [ -n "$ODD_PORTS" ]; then
  echo "⚠ Нетипичные порты, доступные не только с localhost: $ODD_PORTS"
else
  echo "Снаружи слушают только 22/80/443."
fi

hr "ПРОВЕРКА ПРИЛОЖЕНИЯ (HTTP/HTTPS)"
if have curl; then
  echo "http://localhost/      : $(curl -s -o /dev/null -w '%{http_code}, %{time_total} с' --max-time 10 http://localhost/ 2>&1)"
  if [ -n "$SITE_HOST" ]; then
    HTTPS_RES=$(curl -s -o /dev/null -w '%{http_code}, %{time_total} с' --max-time 10 "https://$SITE_HOST/" 2>&1)
    echo "https://$SITE_HOST/ : $HTTPS_RES"
  else
    echo "SITE_HOST не задан — HTTPS не проверен."
  fi
else
  echo "curl не установлен — проверка пропущена."
fi

hr "УПАВШИЕ СЛУЖБЫ systemd"
if have systemctl; then
  systemctl --failed --no-pager --no-legend | sed 's/^/  /' | { grep . || echo "  Нет, всё в порядке."; }
fi

# ---------- 7. Итоговое заключение ----------
# TODO(human): решить, при каких пиках запас считается недостаточным.
# Доступно (только при запуске с --load): PEAK_MEM_MIB — сумма пиков RAM контейнеров, МиБ;
# PEAK_CPU_PCT — сумма пиков CPU (100 = одно ядро); MEM_TOTAL — RAM хоста, кБ; CORES — число ядер.
# Для проблемы: warn=1 и echo "⚠ …". Пока функция ничего не проверяет.
load_verdict() {
  :
}

hr "ИТОГ"
ROOT_USE=$(df / --output=pcent | tail -1 | tr -dc '0-9')
MEM_FREE_PCT=$(( 100 * MEM_AVAIL / MEM_TOTAL ))
[ "$ROOT_USE" -ge 85 ]      && { echo "⚠ Диск / заполнен на ${ROOT_USE}%";              warn=1; }
[ "$MEM_FREE_PCT" -le 15 ]  && { echo "⚠ Доступной памяти всего ${MEM_FREE_PCT}%";      warn=1; }
[ "$SWAP_USED" -gt 0 ]      && { echo "⚠ Задействован swap";                            warn=1; }
[ "$SWAP_TOTAL" -eq 0 ] && [ "$MEM_TOTAL" -lt 4194304 ] \
                            && { echo "⚠ Swap нет, а RAM < 4 ГБ: при пике Blender OOM-killer убьёт процесс"; warn=1; }
[ "$STEAL_PCT" -ge 10 ]     && { echo "⚠ Steal ${STEAL_PCT}% — хост VPS перегружен";     warn=1; }
[ "$DOCKER_BAD" -eq 1 ]     && { echo "⚠ Есть перезапуски контейнеров или OOMKilled";    warn=1; }
[ -n "${ODD_PORTS:-}" ]     && { echo "⚠ Лишние открытые порты: $ODD_PORTS";             warn=1; }
if [ -n "${IMG_MB:-}" ] && [ "${FREE_MB:-0}" -lt "${IMG_MB:-0}" ]; then
  echo "⚠ Свободно ${FREE_MB} МБ, образы ~${IMG_MB} МБ: при деплое (pull до prune) места может не хватить"; warn=1
fi
case "${HTTPS_RES:-200}" in 2*|3*) ;; *) echo "⚠ HTTPS не отвечает штатно: $HTTPS_RES"; warn=1 ;; esac
# сравнение load average с числом ядер (через awk, т.к. число дробное)
awk -v l="$L5" -v c="$CORES" 'BEGIN{exit !(l>c)}' && { echo "⚠ Load average (5 мин) $L5 выше числа ядер $CORES"; warn=1; }
[ "$LOAD_SECS" -gt 0 ] && load_verdict
if [ "$warn" -eq 0 ]; then
  echo "✔ Запас ресурсов в норме."
  [ "$LOAD_SECS" -eq 0 ] && echo "  (это оценка для состояния покоя; пики под нагрузкой — запуск с --load)"
fi
exit 0
