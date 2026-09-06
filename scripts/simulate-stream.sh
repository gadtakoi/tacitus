#!/usr/bin/env bash
#
# Управление имитатором видеопотока — «фальш-камерой», которая публикует
# синтетический RTSP-поток в MediaMTX.
#
#   scripts/simulate-stream.sh start                      # один поток в cam1
#   scripts/simulate-stream.sh start --paths cam1,live    # два потока сразу
#   scripts/simulate-stream.sh start --mode reconnect --up 5 --down 5
#   scripts/simulate-stream.sh status
#   scripts/simulate-stream.sh logs -f
#   scripts/simulate-stream.sh negative                   # MediaMTX обязан отказать
#   scripts/simulate-stream.sh stop
#
# Сервис живёт под профилем compose `simulator` и обычным `docker compose up`
# не поднимается — ни локально, ни на сервере.
#
# Пароль публикатора не является опцией командной строки: argv виден в выводе
# ps и сохраняется в истории оболочки. Он берётся из .env через подстановку
# compose (TACITUS_SIM_PUBLISH_PASSWORD) и в этот скрипт не попадает.
#
# Коды выхода:
#   0 — команда выполнена (для negative: MediaMTX отказал во всех проверках)
#   1 — сценарий не выполнен: путь не стал ready либо MediaMTX принял то,
#       что обязан был отвергнуть
#   2 — окружение непригодно: нет docker, стек не поднят, неверные аргументы

set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly SERVICE="stream-simulator"
readonly PROFILE="simulator"
readonly CONTAINER="tacitus-stream-simulator"

die() {
    printf 'error: %s\n' "$*" >&2
    exit 2
}

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

info() {
    printf '\033[1m==>\033[0m %s\n' "$*"
}

usage() {
    cat <<'EOF'
Использование: scripts/simulate-stream.sh <команда> [опции]

Команды:
  start        Поднять имитатор (профиль compose `simulator`)
  stop         Остановить и удалить контейнер имитатора
  status       Показать состояние путей в MediaMTX и файлов записи
  logs [-f]    Показать логи имитатора
  negative     Одноразовая проверка: MediaMTX обязан отказать при неверном
               пароле, при необъявленном пути и при попытке публиковать под
               учётной записью reader

Опции start:
  --paths a,b        Пути публикации через запятую (по умолчанию cam1).
                     Путь должен быть описан в mediamtx/mediamtx.yml.
  --mode MODE        continuous | reconnect (по умолчанию continuous)
  --size WxH         Разрешение (по умолчанию 1280x720)
  --fps N            Кадров в секунду (по умолчанию 25)
  --bitrate RATE     Битрейт видео (по умолчанию 1500k)
  --no-audio         Не публиковать звуковую дорожку
  --up SEC           Длительность фазы публикации в режиме reconnect
  --down SEC         Длительность паузы в режиме reconnect
  --cycles N         Число циклов reconnect (0 — бесконечно)
  --wait SEC         Сколько ждать готовности путей (по умолчанию 30)
  -h, --help         Показать эту справку

Пароль публикатора задаётся только через .env (TACITUS_SIM_PUBLISH_PASSWORD)
и опцией командной строки быть не может.

Коды выхода: 0 — выполнено, 1 — сценарий не выполнен, 2 — окружение непригодно.
EOF
}

# --- обращение к Control API ------------------------------------------------
#
# Control API MediaMTX не опубликован на хост, поэтому запрос уходит изнутри
# сети — из контейнера app, где учётные данные уже лежат в окружении. Так
# пароль не попадает ни в argv на хосте (виден в ps), ни в историю оболочки.

# Печатает имена сконфигурированных путей, по одному на строку.
mtx_configured_paths() {
    docker compose exec -T app python - <<'PY'
import os
import sys

import httpx

url = os.environ["TACITUS_MTX_API_URL"].rstrip("/") + "/config/paths/list"
response = httpx.get(
    url,
    auth=(os.environ["TACITUS_MTX_API_USER"], os.environ["TACITUS_MTX_API_PASSWORD"]),
    timeout=5.0,
)
if response.status_code != 200:
    sys.stderr.write(f"Control API вернул HTTP {response.status_code}\n")
    raise SystemExit(1)
for item in response.json().get("items", []):
    print(item["name"])
PY
}

# Печатает состояние активного пути одной строкой:
#   <ready> <число дорожек> <принято байт>
# например `true 2 1048576`. Отсутствующий путь (404) — это `absent 0 0`, а не
# ошибка: в режиме reconnect путь исчезает и появляется, и это ожидаемо.
mtx_path_state() {
    docker compose exec -T app python - "$1" <<'PY'
import os
import sys
from urllib.parse import quote

import httpx

name = sys.argv[1]
url = os.environ["TACITUS_MTX_API_URL"].rstrip("/") + f"/paths/get/{quote(name, safe='')}"
response = httpx.get(
    url,
    auth=(os.environ["TACITUS_MTX_API_USER"], os.environ["TACITUS_MTX_API_PASSWORD"]),
    timeout=5.0,
)
if response.status_code == 404:
    print("absent 0 0")
    raise SystemExit(0)
if response.status_code != 200:
    sys.stderr.write(f"Control API вернул HTTP {response.status_code}\n")
    raise SystemExit(1)
payload = response.json()
# Строчные true/false, а не питоновские True/False: значение сравнивается в
# shell, и регистр там — часть контракта.
ready = "true" if payload.get("ready") else "false"
print(ready, len(payload.get("tracks") or []), payload.get("bytesReceived", 0))
PY
}

# --- предусловия ------------------------------------------------------------

require_stack() {
    command -v docker >/dev/null 2>&1 || die "docker не найден в PATH"
    docker compose version >/dev/null 2>&1 || die "docker compose недоступен"
    [ -n "$(docker compose ps -q app 2>/dev/null)" ] || \
        die "контейнер app не запущен — сначала 'docker compose up -d'"
    [ -n "$(docker compose ps -q mediamtx 2>/dev/null)" ] || \
        die "контейнер mediamtx не запущен — сначала 'docker compose up -d'"
}

# Публикация в путь, не описанный в mediamtx.yml, отвергается с 400 Bad
# Request. Проверка здесь превращает невнятную ошибку ffmpeg в конкретное
# сообщение с перечнем доступных путей.
require_configured_paths() {
    local configured requested missing=()
    configured="$(mtx_configured_paths)" || die "не удалось прочитать конфигурацию путей MediaMTX"
    IFS=',' read -ra requested <<<"$1"
    for name in "${requested[@]}"; do
        grep -qxF "$name" <<<"$configured" || missing+=("$name")
    done
    if [ ${#missing[@]} -gt 0 ]; then
        die "путь(и) ${missing[*]} не описаны в mediamtx/mediamtx.yml — MediaMTX ответит 400 Bad Request; доступны: $(tr '\n' ' ' <<<"$configured")"
    fi
}

# --- команды ----------------------------------------------------------------

cmd_start() {
    local paths="cam1" mode="continuous" size="1280x720" fps="25" bitrate="1500k"
    local audio="true" up="30" down="10" cycles="0" wait_seconds="30"

    while [ $# -gt 0 ]; do
        case "$1" in
            --paths)    paths="${2:-}"; shift 2 ;;
            --mode)     mode="${2:-}"; shift 2 ;;
            --size)     size="${2:-}"; shift 2 ;;
            --fps)      fps="${2:-}"; shift 2 ;;
            --bitrate)  bitrate="${2:-}"; shift 2 ;;
            --no-audio) audio="false"; shift ;;
            --up)       up="${2:-}"; shift 2 ;;
            --down)     down="${2:-}"; shift 2 ;;
            --cycles)   cycles="${2:-}"; shift 2 ;;
            --wait)     wait_seconds="${2:-}"; shift 2 ;;
            -h|--help)  usage; exit 0 ;;
            *)          die "неизвестная опция: $1" ;;
        esac
    done

    case "$mode" in
        continuous|reconnect) ;;
        *) die "неверный режим '$mode': ожидается continuous или reconnect" ;;
    esac

    require_stack
    require_configured_paths "$paths"

    export TACITUS_SIM_MODE="$mode"
    export TACITUS_SIM_PATHS="$paths"
    export TACITUS_SIM_SIZE="$size"
    export TACITUS_SIM_FPS="$fps"
    export TACITUS_SIM_BITRATE="$bitrate"
    export TACITUS_SIM_AUDIO="$audio"
    export TACITUS_SIM_UP_SECONDS="$up"
    export TACITUS_SIM_DOWN_SECONDS="$down"
    export TACITUS_SIM_CYCLES="$cycles"

    # Предыдущий запуск мог остаться: compose не пересоздаёт контейнер, если
    # изменились только переменные окружения, взятые из процесса.
    docker compose --profile "$PROFILE" rm -sf "$SERVICE" >/dev/null 2>&1 || true

    info "запуск имитатора: режим $mode, пути $paths, источник ${size}@${fps} $bitrate"
    print_disk_estimate "$paths" "$bitrate"
    docker compose --profile "$PROFILE" up -d "$SERVICE" >/dev/null

    wait_for_ready "$paths" "$wait_seconds"
}

# Непрерывная публикация наполняет ./recordings; оператор должен понимать
# стоимость до того, как запустит её на ночь.
print_disk_estimate() {
    local paths="$1" bitrate="$2" kbps streams mb_per_min
    kbps="${bitrate%[kK]}"
    if [ "$kbps" = "$bitrate" ]; then
        return 0
    fi
    streams="$(tr ',' '\n' <<<"$paths" | grep -c .)"
    mb_per_min=$(( kbps * 60 * streams / 8 / 1024 ))
    info "оценка записи: ~${mb_per_min} МБ/мин суммарно ($streams поток(ов))"
}

# Обёртка, возвращающая 0, когда на самом деле ничего не публикуется, хуже её
# отсутствия: ждём фактического ready, а не факта запуска контейнера.
wait_for_ready() {
    local paths="$1" timeout="$2" deadline name pending
    deadline=$(( $(date +%s) + timeout ))

    while [ "$(date +%s)" -lt "$deadline" ]; do
        pending=""
        for name in ${paths//,/ }; do
            [ "$(mtx_path_state "$name" 2>/dev/null | awk '{print $1}')" = "true" ] || \
                pending="$pending $name"
        done
        if [ -z "$pending" ]; then
            info "поток(и) готовы: $paths"
            return 0
        fi
        sleep 1
    done

    printf 'последние строки лога имитатора:\n' >&2
    docker compose --profile "$PROFILE" logs --tail 20 "$SERVICE" >&2 || true
    fail "пути$pending не стали ready за ${timeout}с"
}

cmd_stop() {
    require_stack
    info "остановка имитатора"
    # Только сам сервис: `docker compose down` снял бы весь стек.
    docker compose --profile "$PROFILE" rm -sf "$SERVICE" >/dev/null
    info "контейнер $CONTAINER удалён"
}

cmd_status() {
    require_stack
    local paths="${TACITUS_SIM_PATHS:-cam1,live}" name first second files
    local ready tracks bytes_before bytes_after

    if [ -z "$(docker compose --profile "$PROFILE" ps -q "$SERVICE" 2>/dev/null)" ]; then
        info "контейнер $CONTAINER не запущен"
    fi

    printf '%-12s %-7s %-9s %-24s %s\n' "ПУТЬ" "READY" "ДОРОЖЕК" "ПРИНЯТО БАЙТ (+3с)" "ФАЙЛОВ"
    for name in ${paths//,/ }; do
        first="$(mtx_path_state "$name" 2>/dev/null || echo "? 0 0")"
        # Один замер показал бы, что путь существует, но не что он живёт.
        # Второй через 3 с делает рост принятых байт видимым глазом.
        sleep 3
        second="$(mtx_path_state "$name" 2>/dev/null || echo "? 0 0")"
        # Каталог пути появляется только после первой удачной записи, а `find`
        # по отсутствующему каталогу возвращает 1 — под `set -o pipefail` это
        # уронило бы весь скрипт вместо честного «файлов: 0».
        files="$({ find "$REPO_ROOT/recordings/$name" -type f 2>/dev/null || true; } | wc -l | tr -d ' ')"
        read -r _ _ bytes_before <<<"$first"
        read -r ready tracks bytes_after <<<"$second"
        printf '%-12s %-7s %-9s %-24s %s\n' \
            "$name" "$ready" "$tracks" "$bytes_before → $bytes_after" "$files"
    done
}

cmd_logs() {
    require_stack
    docker compose --profile "$PROFILE" logs "$@" "$SERVICE"
}

cmd_negative() {
    require_stack
    info "негативная проверка: MediaMTX обязан отказать во всех трёх случаях"
    # --rm: одноразовый прогон, а не постоянный сервис. Код возврата скрипта
    # точки входа доезжает сюда как есть (0 / 1 / 2).
    TACITUS_SIM_MODE=negative \
        docker compose --profile "$PROFILE" run --rm "$SERVICE"
}

# --- разбор команды ---------------------------------------------------------

[ $# -gt 0 ] || { usage; exit 2; }

command="$1"
shift

case "$command" in
    start)    cmd_start "$@" ;;
    stop)     cmd_stop "$@" ;;
    status)   cmd_status "$@" ;;
    logs)     cmd_logs "$@" ;;
    negative) cmd_negative "$@" ;;
    -h|--help) usage; exit 0 ;;
    *)        die "неизвестная команда '$command' (см. --help)" ;;
esac
