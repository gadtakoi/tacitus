#!/bin/sh
#
# Точка входа сервиса stream-simulator (профиль compose `simulator`).
#
# Публикует в MediaMTX синтетический RTSP-поток — «фальш-камеру», — чтобы
# проверить конвейер публикация → путь MediaMTX → GET /streams → запись →
# архив без настоящего оборудования.
#
# Запускать напрямую не нужно: сервисом управляет scripts/simulate-stream.sh.
#
# Почему POSIX sh, а не bash и не python: контейнер работает на образе
# bluenviron/mediamtx:1.15.1-ffmpeg, в котором есть только busybox ash. Ни
# bash, ни python в образе нет, а доустанавливать что-либо в рантайме проект
# запрещает (tests/test_compose_pinning.py). Поэтому здесь нет ни `[[`, ни
# `local`, ни `pipefail`.
#
# Режимы (TACITUS_SIM_MODE):
#   continuous — по одному непрерывному потоку на каждый путь из
#                TACITUS_SIM_PATHS (несколько путей = несколько потоков сразу)
#   reconnect  — цикл «публикуем UP_SECONDS → отключаемся на DOWN_SECONDS»,
#                проверяет переход пути ready → not ready → ready
#   negative   — одноразовая проверка: MediaMTX обязан отказать при неверном
#                пароле, при пути без конфигурации и при попытке публиковать
#                под учётной записью reader. Завершается сам.
#
# Пароль передаётся только через окружение и никогда не печатается: в лог
# уходит URL с заменой пароля на ***. Уровень логов ffmpeg зафиксирован на
# `error` — на `verbose`/`debug` ffmpeg печатает URL целиком, вместе с паролем.
#
# Коды выхода:
#   0 — работа завершена штатно (для negative: MediaMTX отказал во всех трёх)
#   1 — сценарий не выполнен (например, MediaMTX принял неверные учётные данные)
#   2 — окружение непригодно (нет пароля, MediaMTX недоступен, неверный режим)

set -eu

MODE="${TACITUS_SIM_MODE:-continuous}"
PATHS="${TACITUS_SIM_PATHS:-cam1}"
HOST="${TACITUS_SIM_MTX_HOST:-mediamtx}"
PORT="${TACITUS_SIM_MTX_PORT:-8554}"
PUBLISH_USER="${TACITUS_SIM_PUBLISH_USER:-publisher}"
PUBLISH_PASSWORD="${TACITUS_SIM_PUBLISH_PASSWORD:-}"
READ_USER="${TACITUS_SIM_READ_USER:-reader}"
READ_PASSWORD="${TACITUS_SIM_READ_PASSWORD:-}"
SIZE="${TACITUS_SIM_SIZE:-1280x720}"
FPS="${TACITUS_SIM_FPS:-25}"
BITRATE="${TACITUS_SIM_BITRATE:-1500k}"
GOP_SECONDS="${TACITUS_SIM_GOP_SECONDS:-1}"
AUDIO="${TACITUS_SIM_AUDIO:-true}"
UP_SECONDS="${TACITUS_SIM_UP_SECONDS:-30}"
DOWN_SECONDS="${TACITUS_SIM_DOWN_SECONDS:-10}"
CYCLES="${TACITUS_SIM_CYCLES:-0}"
NEGATIVE_PATH="${TACITUS_SIM_NEGATIVE_PATH:-no-such-stream}"

# Пороги перезапуска в режиме continuous: пять отказов подряд за минуту — это
# уже не «MediaMTX моргнул», а сломанное окружение, и оно обязано быть заметно.
readonly RESTART_BACKOFF_SECONDS=2
readonly MAX_CONSECUTIVE_FAILURES=5
readonly FAILURE_WINDOW_SECONDS=60

# Каталог для stderr ffmpeg. Файловая система контейнера смонтирована ro,
# писать можно только в tmpfs /tmp (см. docker-compose.yml).
readonly WORK_DIR=/tmp

timestamp() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }

log() { printf '%s simulator %s\n' "$(timestamp)" "$*"; }

# Код 2 — «окружение непригодно». Отличать его от кода 1 важно: провал
# сценария и невозможность его выполнить требуют разных действий.
die() { printf '%s simulator error: %s\n' "$(timestamp)" "$*" >&2; exit 2; }

# Рабочий URL публикации. Никогда не попадает в лог: его печатает только
# ffmpeg в argv внутри контейнера (устранить нельзя, см. ADR-014).
publish_url() { printf 'rtsp://%s:%s@%s:%s/%s' "$1" "$2" "$HOST" "$PORT" "$3"; }

# URL для лога: пароль заменяется, чтобы секрет не попал ни в docker logs, ни
# в вывод CI (.ai/project/logging-rules.md).
redacted_url() { printf 'rtsp://%s:***@%s:%s/%s' "$1" "$HOST" "$PORT" "$2"; }

# Имена путей приходят из окружения и подставляются прямо в URL, поэтому
# алфавит ограничен тем же набором, что и _validate_stream_name в
# app/schemas/streams.py. Иначе имя вида `cam1;rm -rf /` попало бы в команду.
validate_path_name() {
    case "$1" in
        "" ) die "пустое имя пути в TACITUS_SIM_PATHS" ;;
        *[!A-Za-z0-9_-]* ) die "недопустимое имя пути '$1': разрешены только A-Za-z0-9_-" ;;
    esac
}

# Одна попытка публикации. $1 — путь, $2 — длительность в секундах (0 —
# бесконечно), $3 — логин, $4 — пароль. Возвращает код возврата ffmpeg.
#
# -re перед КАЖДЫМ входом lavfi обязателен: без него генератор работает со
# скоростью процессора, и «камера» публикует час видео за секунды — записи
# разрастаются, а рост bytesReceived перестаёт быть признаком жизни.
#
# -g/-keyint_min/-sc_threshold тоже не косметика: MediaMTX начинает сегмент
# fmp4 с ключевого кадра, и при дефолтном GOP ffmpeg (250 кадров ≈ 10 с)
# первый файл записи появляется с большой задержкой.
publish_once() {
    sim_path="$1"
    duration="$2"
    user="$3"
    password="$4"

    gop=$((FPS * GOP_SECONDS))
    set -- \
        -hide_banner -loglevel error -nostdin \
        -re -f lavfi -i "testsrc2=size=${SIZE}:rate=${FPS}"
    if [ "$AUDIO" = "true" ]; then
        set -- "$@" -re -f lavfi -i "sine=frequency=1000:sample_rate=48000"
    fi
    if [ "$duration" != "0" ]; then
        set -- "$@" -t "$duration"
    fi
    set -- "$@" \
        -c:v libx264 -preset veryfast -tune zerolatency -pix_fmt yuv420p \
        -b:v "$BITRATE" -g "$gop" -keyint_min "$gop" -sc_threshold 0
    if [ "$AUDIO" = "true" ]; then
        set -- "$@" -c:a aac -b:a 64k -ar 48000 -ac 1
    fi
    # -rtsp_transport tcp соответствует MTX_RTSPTRANSPORTS=tcp из compose:
    # рассогласование дешевле сделать невозможным, чем потом отлаживать.
    set -- "$@" -f rtsp -rtsp_transport tcp "$(publish_url "$user" "$password" "$sim_path")"

    ffmpeg "$@"
}

# --- preflight --------------------------------------------------------------

case "$MODE" in
    continuous|reconnect|negative) ;;
    *) die "неизвестный режим '$MODE': ожидается continuous, reconnect или negative" ;;
esac

# Пустой пароль обязан останавливать работу здесь. Иначе MediaMTX ответит 401,
# и отказ по конфигурации станет неотличим от сетевой ошибки.
[ -n "$PUBLISH_PASSWORD" ] || die "не задан TACITUS_SIM_PUBLISH_PASSWORD"

# Та же проба, что в healthcheck сервиса mediamtx.
nc -z "$HOST" "$PORT" || die "MediaMTX недоступен на $HOST:$PORT"

# --- режим negative ---------------------------------------------------------

# «Сервер отказал» и «сервера нет» дают у ffmpeg один и тот же признак —
# ненулевой код возврата, — поэтому решает stderr. Порядок проверок важен:
#   Connection refused / No route to host / timed out → до сервера не дошли:
#     проверка НЕ выполнена, а не пройдена
#   40x → MediaMTX ответил и отказал: это успех
#   код 0 → MediaMTX ПРИНЯЛ то, что обязан был отвергнуть: провал
#
# Код возврата ffmpeg 8 намеренно не проверяется: это усечённый
# AVERROR_HTTP_UNAUTHORIZED, он одинаков и для 401, и для 400, и является
# деталью конкретной сборки ffmpeg.
classify() {
    if [ "$1" -eq 0 ]; then
        printf 'accepted'
        return
    fi
    if grep -qE 'Connection refused|No route to host|Connection timed out|Network is unreachable' "$2"; then
        printf 'unreachable'
        return
    fi
    if grep -qE '40[0-9] (Unauthorized|Bad Request|Forbidden|Not Found)' "$2"; then
        printf 'rejected'
        return
    fi
    printf 'unknown'
}

# Одна негативная проверка. $1 — описание, $2 — путь, $3 — логин, $4 — пароль.
# Возвращает 0, если MediaMTX отказал, 1 — если принял, 2 — если не ответил.
expect_rejected() {
    label="$1"
    sim_path="$2"
    user="$3"
    password="$4"
    stderr_file="$WORK_DIR/negative.$$"

    log "проверка: $label → $(redacted_url "$user" "$sim_path")"

    # Кадр 160x120@5 на одну секунду: отвергнутый ANNOUNCE не пишет ничего,
    # поэтому дорогой источник здесь не нужен.
    saved_size="$SIZE"; saved_fps="$FPS"; saved_audio="$AUDIO"
    SIZE=160x120; FPS=5; AUDIO=false
    ff_rc=0
    publish_once "$sim_path" 1 "$user" "$password" 2>"$stderr_file" || ff_rc=$?
    SIZE="$saved_size"; FPS="$saved_fps"; AUDIO="$saved_audio"

    verdict="$(classify "$ff_rc" "$stderr_file")"
    case "$verdict" in
        rejected)
            log "  OK: MediaMTX отказал"
            rm -f "$stderr_file"
            return 0
            ;;
        accepted)
            log "  ПРОВАЛ: MediaMTX принял публикацию, которую обязан был отвергнуть"
            rm -f "$stderr_file"
            return 1
            ;;
        unreachable)
            log "  НЕ ВЫПОЛНЕНО: MediaMTX недоступен"
            rm -f "$stderr_file"
            return 2
            ;;
        *)
            # Неклассифицируемый отказ пройденной проверкой не считается.
            log "  НЕ ВЫПОЛНЕНО: непонятная ошибка ffmpeg (код $ff_rc)"
            sed 's/^/    /' "$stderr_file" >&2 || true
            rm -f "$stderr_file"
            return 2
            ;;
    esac
}

# Счётчики итога негативного прогона. Обновляются через record_check, чтобы
# код возврата expect_rejected нигде не читался «на расстоянии».
failures=0
blocked=0

record_check() {
    check_rc=0
    expect_rejected "$1" "$2" "$3" "$4" || check_rc=$?
    case "$check_rc" in
        1) failures=$((failures + 1)) ;;
        2) blocked=$((blocked + 1)) ;;
    esac
}

run_negative() {
    [ -n "$READ_PASSWORD" ] || die "не задан TACITUS_SIM_READ_PASSWORD (нужен для проверки 3)"

    first_path="${PATHS%%,*}"
    validate_path_name "$first_path"
    validate_path_name "$NEGATIVE_PATH"

    record_check "неверный пароль публикатора" \
        "$first_path" "$PUBLISH_USER" "${PUBLISH_PASSWORD}-wrong"

    record_check "путь без конфигурации в mediamtx.yml" \
        "$NEGATIVE_PATH" "$PUBLISH_USER" "$PUBLISH_PASSWORD"

    # Разделение прав по действиям из ADR-012 должно работать в рантайме, а не
    # только в YAML: reader не имеет действия publish.
    record_check "учётная запись reader пытается публиковать" \
        "$first_path" "$READ_USER" "$READ_PASSWORD"

    if [ "$blocked" -gt 0 ]; then
        log "негативная проверка не выполнена: $blocked из 3 не дошли до MediaMTX"
        exit 2
    fi
    if [ "$failures" -gt 0 ]; then
        log "негативная проверка провалена: MediaMTX принял $failures из 3 недопустимых публикаций"
        exit 1
    fi
    log "негативная проверка пройдена: MediaMTX отказал во всех трёх случаях"
    exit 0
}

# --- режимы публикации ------------------------------------------------------

# Непрерывная публикация в один путь с перезапуском при обрыве.
run_continuous_path() {
    sim_path="$1"
    fail_streak=0
    window_start="$(date -u '+%s')"

    while :; do
        log "публикация $(redacted_url "$PUBLISH_USER" "$sim_path")"
        ff_rc=0
        publish_once "$sim_path" 0 "$PUBLISH_USER" "$PUBLISH_PASSWORD" || ff_rc=$?
        if [ "$ff_rc" -eq 0 ]; then
            log "$sim_path: ffmpeg завершился штатно"
        else
            log "$sim_path: ffmpeg прервался (код $ff_rc)"
        fi

        now="$(date -u '+%s')"
        if [ $((now - window_start)) -gt "$FAILURE_WINDOW_SECONDS" ]; then
            fail_streak=0
            window_start="$now"
        fi
        fail_streak=$((fail_streak + 1))
        if [ "$fail_streak" -ge "$MAX_CONSECUTIVE_FAILURES" ]; then
            log "$sim_path: $fail_streak отказов за ${FAILURE_WINDOW_SECONDS}с — прекращаю"
            exit 1
        fi
        sleep "$RESTART_BACKOFF_SECONDS"
    done
}

# Цикл «публикуем UP → пропадаем на DOWN». Проверяет переход пути
# ready → not ready → ready и появление нового сегмента записи после паузы.
run_reconnect_path() {
    sim_path="$1"
    cycle=0

    while :; do
        cycle=$((cycle + 1))
        log "$sim_path: цикл $cycle, публикация ${UP_SECONDS}с"
        publish_once "$sim_path" "$UP_SECONDS" "$PUBLISH_USER" "$PUBLISH_PASSWORD" || \
            log "$sim_path: ffmpeg прервался досрочно"
        if [ "$CYCLES" != "0" ] && [ "$cycle" -ge "$CYCLES" ]; then
            log "$sim_path: выполнено циклов: $cycle"
            return 0
        fi
        log "$sim_path: отключение на ${DOWN_SECONDS}с"
        sleep "$DOWN_SECONDS"
    done
}

# --- запуск -----------------------------------------------------------------

if [ "$MODE" = "negative" ]; then
    run_negative
fi

pids=""
# TERM/INT должны давать чистое отключение публикатора: SIGKILL через 10 с
# выглядел бы как обрыв связи, а это отдельный сценарий (reconnect).
trap 'log "остановка"; for p in $pids; do kill "$p" 2>/dev/null || true; done; exit 0' TERM INT

log "режим $MODE, пути: $PATHS, источник ${SIZE}@${FPS} ${BITRATE}, звук: $AUDIO"

old_ifs="$IFS"
IFS=','
for sim_path in $PATHS; do
    IFS="$old_ifs"
    validate_path_name "$sim_path"
    if [ "$MODE" = "reconnect" ]; then
        run_reconnect_path "$sim_path" &
    else
        run_continuous_path "$sim_path" &
    fi
    pids="$pids $!"
    IFS=','
done
IFS="$old_ifs"

# Ненулевой код любой из подоболочек должен доехать до docker: `wait` без
# аргумента вернул бы 0 даже после падения.
status=0
for p in $pids; do
    wait "$p" || status=$?
done
exit "$status"
