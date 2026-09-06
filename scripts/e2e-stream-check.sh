#!/usr/bin/env bash
#
# Сквозная проверка конвейера видеопотока на локальном стеке docker compose.
#
#   scripts/e2e-stream-check.sh                 # весь конвейер, кроме архива
#   scripts/e2e-stream-check.sh --strict        # предупреждения считать ошибками
#   scripts/e2e-stream-check.sh --with-archive  # плюс выгрузка в S3 (см. ниже)
#
# Проверяется вся цепочка: публикация → путь MediaMTX → GET /streams → запись
# на диск → (опционально) архив в S3. Источник — имитатор из
# scripts/simulate-stream.sh, настоящее оборудование не нужно.
#
# В отличие от scripts/healthcheck.sh этот скрипт МЕНЯЕТ состояние: он
# публикует поток, из-за чего появляются записи на диске, и заводит тестового
# пользователя через API. Направлять его на production нельзя.
#
# Коды выхода:
#   0 — конвейер работает, FAIL нет
#   1 — есть FAIL (или WARN при --strict)
#   2 — проверку не удалось выполнить: нет docker/curl, стек не поднят,
#       не заданы учётные данные публикатора
#
# Молчаливого пропуска здесь нет: невыполнимая проверка — это код 2, а не 0.

set -uo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly SIMULATE="$REPO_ROOT/scripts/simulate-stream.sh"
readonly PATHS="cam1,live"
# Лёгкий источник: сквозная проверка не должна наполнять диск.
readonly SIM_ARGS=(--paths "$PATHS" --size 640x480 --fps 15 --bitrate 500k)

pass_count=0
warn_count=0
fail_count=0
strict=false
with_archive=false

if [ -t 1 ]; then
    C_BOLD=$'\033[1m'; C_OK=$'\033[32m'; C_WARN=$'\033[33m'
    C_FAIL=$'\033[31m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
    C_BOLD=""; C_OK=""; C_WARN=""; C_FAIL=""; C_DIM=""; C_OFF=""
fi

section() { printf '\n%s%s%s\n' "$C_BOLD" "$*" "$C_OFF"; }
ok()      { pass_count=$((pass_count + 1)); printf '  %s[ OK ]%s %s\n' "$C_OK" "$C_OFF" "$*"; }
warn()    { warn_count=$((warn_count + 1)); printf '  %s[WARN]%s %s\n' "$C_WARN" "$C_OFF" "$*"; }
bad()     { fail_count=$((fail_count + 1)); printf '  %s[FAIL]%s %s\n' "$C_FAIL" "$C_OFF" "$*"; }
note()    { printf '  %s       %s%s\n' "$C_DIM" "$*" "$C_OFF"; }

die() { printf 'error: %s\n' "$*" >&2; exit 2; }

usage() {
    cat <<'EOF'
Использование: scripts/e2e-stream-check.sh [опции]

  --strict          Считать предупреждения ошибками (код выхода 1)
  --with-archive    Дополнительно проверить выгрузку записей в S3.
                    ВНИМАНИЕ: запускает архиватор с MAX_AGE_DAYS=0, то есть
                    выгружает и УДАЛЯЕТ все локальные записи. Переход
                    «запись → архив» необратим (CONTEXT.md). Работает только
                    при TACITUS_ENVIRONMENT=development.
  -h, --help        Показать эту справку

Требуется поднятый стек (`docker compose up -d`) и заданный
TACITUS_SIM_PUBLISH_PASSWORD (обычно берётся из .env).
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --strict)       strict=true; shift ;;
        --with-archive) with_archive=true; shift ;;
        -h|--help)      usage; exit 0 ;;
        *)              die "неизвестная опция: $1 (см. --help)" ;;
    esac
done

# Ожидание по дедлайну, а не sleep наугад: проверка не «ждёт достаточно
# долго», а «ждёт, пока условие не выполнится, но не дольше таймаута».
wait_until() {
    local deadline
    deadline=$(( $(date +%s) + $1 )); shift
    while [ "$(date +%s)" -lt "$deadline" ]; do
        "$@" && return 0
        sleep 1
    done
    return 1
}

# Control API MediaMTX наружу не опубликован — запрос уходит из контейнера app.
mtx_path_state() {
    docker compose exec -T app python - "$1" <<'PY'
import os
import sys
from urllib.parse import quote

import httpx

url = os.environ["TACITUS_MTX_API_URL"].rstrip("/") + f"/paths/get/{quote(sys.argv[1], safe='')}"
response = httpx.get(
    url,
    auth=(os.environ["TACITUS_MTX_API_USER"], os.environ["TACITUS_MTX_API_PASSWORD"]),
    timeout=5.0,
)
if response.status_code == 404:
    print("absent 0 0")
    raise SystemExit(0)
if response.status_code != 200:
    raise SystemExit(1)
payload = response.json()
print("true" if payload.get("ready") else "false",
      len(payload.get("tracks") or []),
      payload.get("bytesReceived", 0))
PY
}

path_field() { mtx_path_state "$1" 2>/dev/null | awk -v n="$2" '{print $n}'; }
path_ready() { [ "$(path_field "$1" 1)" = "true" ]; }
path_absent_or_down() { [ "$(path_field "$1" 1)" != "true" ]; }

# `find` по отсутствующему каталогу возвращает 1; под pipefail это уронило бы
# скрипт вместо честного нуля.
count_recordings() {
    { find "$REPO_ROOT/recordings/$1" -type f 2>/dev/null || true; } | wc -l | tr -d ' '
}

# Предикат для wait_until: файлов у пути $1 стало больше, чем $2.
more_recordings_than() { [ "$(count_recordings "$1")" -gt "$2" ]; }

api() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

printf '%sСквозная проверка конвейера потоков%s  (%s)\n' \
    "$C_BOLD" "$C_OFF" "$(date -Is 2>/dev/null || date)"

# Имитатор не должен пережить скрипт даже при Ctrl-C: иначе он продолжит
# наполнять recordings/ уже после того, как проверка завершилась.
cleanup() { bash "$SIMULATE" stop >/dev/null 2>&1 || true; }
trap cleanup EXIT

# --- 0. Предусловия ---------------------------------------------------------

section "0. Предусловия"

command -v docker >/dev/null 2>&1 || die "docker не найден в PATH"
command -v curl >/dev/null 2>&1 || die "curl не найден в PATH"
docker compose version >/dev/null 2>&1 || die "docker compose недоступен"
[ -n "${TACITUS_SIM_PUBLISH_PASSWORD:-}" ] || \
    die "не задан TACITUS_SIM_PUBLISH_PASSWORD (см. .env.example)"

for service in app mediamtx; do
    [ -n "$(docker compose ps -q "$service" 2>/dev/null)" ] || \
        die "контейнер $service не запущен — сначала 'docker compose up -d'"
done
ok "стек поднят, docker и curl доступны"

base_url="http://localhost:${NGINX_HOST_PORT:-80}"
health="$(api "$base_url/health")"
if [ "$health" != "200" ]; then
    # 502 почти всегда означает, что nginx закешировал адрес контейнера app при
    # старте, а app с тех пор пересоздавался и получил другой IP. Проверка
    # конвейера тут ни при чём, поэтому подсказываем лечение сразу.
    if [ "$health" = "502" ]; then
        printf 'error: %s/health вернул 502\n' "$base_url" >&2
        printf '       nginx держит устаревший адрес upstream — `docker compose restart nginx`\n' >&2
        exit 2
    fi
    die "$base_url/health вернул $health, а не 200"
fi
ok "приложение отвечает на $base_url/health"

# --- 1. Базовый замер -------------------------------------------------------

section "1. Базовый замер записей"

declare -A baseline
for name in ${PATHS//,/ }; do
    baseline["$name"]="$(count_recordings "$name")"
    note "recordings/$name: ${baseline[$name]} файл(ов) до запуска"
done

# --- 2. Запуск имитатора ----------------------------------------------------

section "2. Запуск имитатора"

if bash "$SIMULATE" start "${SIM_ARGS[@]}" --wait 40 >/dev/null 2>&1; then
    ok "имитатор запущен, пути $PATHS готовы"
else
    bad "имитатор не поднял потоки за 40с"
    note "диагностика: bash scripts/simulate-stream.sh logs"
    exit 1
fi

# --- 3. MediaMTX видит потоки ----------------------------------------------

section "3. Состояние путей в MediaMTX"

for name in ${PATHS//,/ }; do
    if path_ready "$name"; then
        ok "$name: ready"
    else
        bad "$name: не ready по данным Control API"
    fi
    tracks="$(path_field "$name" 2)"
    if [ "${tracks:-0}" -ge 2 ]; then
        ok "$name: дорожек $tracks (видео + звук)"
    else
        bad "$name: дорожек $tracks, ожидалось 2"
    fi
done

# --- 4. Аутентификация ------------------------------------------------------

section "4. Получение JWT"

e2e_user="e2e-sim-$(date +%s)"
e2e_pass='E2eSim123!pass'
creds="{\"username\":\"$e2e_user\",\"password\":\"$e2e_pass\"}"

code="$(api -X POST -H 'Content-Type: application/json' -d "$creds" "$base_url/auth/register")"
if [ "$code" = "201" ]; then
    ok "пользователь $e2e_user зарегистрирован"
else
    bad "регистрация вернула $code, ожидалось 201"
fi

token="$(curl -s -X POST -H 'Content-Type: application/json' -d "$creds" \
    "$base_url/auth/login" | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p')"
if [ -n "$token" ]; then
    ok "access_token получен"
else
    bad "логин не вернул access_token"
    exit 1
fi

# --- 5. GET /streams --------------------------------------------------------

section "5. Контракт GET /api/streams"

streams_body="$(curl -s -H "Authorization: Bearer $token" "$base_url/api/streams")"
for name in ${PATHS//,/ }; do
    if grep -q "\"name\":\"$name\"" <<<"$streams_body"; then
        ok "$name присутствует в ответе /api/streams"
    else
        bad "$name отсутствует в ответе /api/streams"
    fi
done

# Псевдонимы схем нужны для разбора camelCase от MediaMTX, но наружу API
# отдаёт snake_case (response_model_by_alias=False). Контракт, который
# tests/test_streams_router.py фиксирует статически, здесь подтверждается
# на живом ответе.
if grep -q '"bytes_received"' <<<"$streams_body"; then
    ok "ключи ответа в snake_case (bytes_received)"
elif grep -q '"bytesReceived"' <<<"$streams_body"; then
    bad "в ответе camelCase (bytesReceived) — псевдонимы MediaMTX протекли наружу"
else
    warn "в ответе нет поля bytes_received — проверить схему StreamItem"
fi

# --- 6. Потоки живые --------------------------------------------------------

section "6. Рост принятых байт"

for name in ${PATHS//,/ }; do
    before="$(path_field "$name" 3)"
    sleep 3
    after="$(path_field "$name" 3)"
    if [ "${after:-0}" -gt "${before:-0}" ]; then
        ok "$name: принято байт $before → $after"
    else
        bad "$name: принятые байты не растут ($before → $after)"
    fi
done

# --- 7. Запись на диск ------------------------------------------------------

section "7. Запись на диск"

recordings_ok=true
for name in ${PATHS//,/ }; do
    if wait_until 30 more_recordings_than "$name" "${baseline[$name]}"; then
        ok "recordings/$name: появились новые файлы ($(count_recordings "$name") шт.)"
    else
        bad "recordings/$name: за 30с не появилось ни одного нового файла"
        recordings_ok=false
    fi
done

# Молчаливый отказ записи почти всегда упирается в права на каталог: MediaMTX
# работает под uid 1000, и если ./recordings принадлежит root, поток
# принимается и отдаётся, а на диск не попадает ничего. Ошибка видна только в
# логах MediaMTX, поэтому подсказку выводим сразу.
if [ "$recordings_ok" = false ]; then
    if docker compose logs --tail 200 mediamtx 2>/dev/null | grep -q 'permission denied'; then
        note "причина: MediaMTX пишет от uid 1000, а ./recordings ему недоступен"
        note "в логах MediaMTX есть 'mkdir /recordings/...: permission denied'"
        note "на Linux: sudo chown -R 1000:1000 ./recordings"
    fi
fi

# --- 8. Переподключение -----------------------------------------------------

section "8. Обрыв и переподключение"

bash "$SIMULATE" stop >/dev/null 2>&1 || true
bash "$SIMULATE" start --paths cam1 --mode reconnect --up 5 --down 5 --cycles 2 \
    --size 320x240 --fps 10 --bitrate 200k --wait 30 >/dev/null 2>&1 || true

if wait_until 20 path_absent_or_down cam1; then
    ok "cam1 перешёл в состояние «публикатор пропал»"
else
    bad "cam1 не перестал быть ready за 20с — обрыв не воспроизвёлся"
fi
if wait_until 20 path_ready cam1; then
    ok "cam1 снова ready после переподключения"
else
    bad "cam1 не восстановился за 20с"
fi

# --- 9. Негативный сценарий -------------------------------------------------

section "9. MediaMTX отвергает недопустимую публикацию"

bash "$SIMULATE" stop >/dev/null 2>&1 || true
negative_rc=0
bash "$SIMULATE" negative >/dev/null 2>&1 || negative_rc=$?
case "$negative_rc" in
    0) ok "MediaMTX отказал во всех трёх случаях (пароль, путь, чужое действие)" ;;
    1) bad "MediaMTX принял публикацию, которую обязан был отвергнуть" ;;
    *) bad "негативную проверку не удалось выполнить (код $negative_rc)" ;;
esac

# Отказ не должен путаться с мёртвым сервером: убеждаемся, что MediaMTX жив.
bash "$SIMULATE" start --paths cam1 --size 320x240 --fps 10 --bitrate 200k --wait 30 \
    >/dev/null 2>&1 || true
if wait_until 20 path_ready cam1; then
    ok "после отказов MediaMTX по-прежнему принимает корректную публикацию"
else
    bad "MediaMTX не принимает публикацию после негативных проверок"
fi

# --- 10. Архив в S3 ---------------------------------------------------------

if [ "$with_archive" = true ]; then
    section "10. Выгрузка записей в архив (S3)"

    if [ "${TACITUS_ENVIRONMENT:-development}" != "development" ]; then
        die "--with-archive работает только при TACITUS_ENVIRONMENT=development"
    fi
    printf '  %sВНИМАНИЕ: архиватор выгрузит и УДАЛИТ все локальные записи.%s\n' \
        "$C_WARN" "$C_OFF"
    note "переход «запись → архив» необратим (CONTEXT.md)"

    bash "$SIMULATE" stop >/dev/null 2>&1 || true
    # Сегмент закрывается только когда публикатор ушёл: иначе архиватор
    # выгрузил бы файл, в который MediaMTX ещё пишет.
    wait_until 20 path_absent_or_down cam1 || true

    if docker compose exec -T -e MAX_AGE_DAYS=0 mediamtx-cleanup \
        python /app/script.py >/dev/null 2>&1; then
        ok "архиватор отработал"
    else
        bad "архиватор завершился с ошибкой"
    fi

    archived="$(docker compose exec -T app python - <<'PY'
import os

import boto3

client = boto3.client(
    "s3",
    endpoint_url=os.environ["TACITUS_S3_ENDPOINT"],
    aws_access_key_id=os.environ["TACITUS_S3_ACCESS_KEY"],
    aws_secret_access_key=os.environ["TACITUS_S3_SECRET_KEY"],
)
response = client.list_objects_v2(
    Bucket=os.environ["TACITUS_S3_BUCKET"], Prefix="mediamtx-archives/"
)
print(response.get("KeyCount", 0))
PY
)"
    if [ "${archived:-0}" -gt 0 ]; then
        ok "в S3 под mediamtx-archives/ лежит объектов: $archived"
    else
        bad "в S3 под mediamtx-archives/ ничего не появилось"
    fi

    if [ "$(count_recordings cam1)" = "0" ]; then
        ok "локальные записи cam1 удалены после выгрузки"
    else
        warn "локальные записи cam1 остались — проверить MAX_SIZE_GB/MAX_AGE_DAYS"
    fi
fi

# --- Итог -------------------------------------------------------------------

section "Итог"
printf '  OK: %s   WARN: %s   FAIL: %s\n' "$pass_count" "$warn_count" "$fail_count"

if [ "$fail_count" -gt 0 ]; then
    exit 1
fi
if [ "$strict" = true ] && [ "$warn_count" -gt 0 ]; then
    exit 1
fi
exit 0
