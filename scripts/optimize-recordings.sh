#!/bin/sh
# Прореживание статичных кадров в закрытых сегментах записи.
#
# Камера, смотрящая на неподвижную сцену, пишет в архив десятки тысяч кадров в
# час, отличающихся только шумом матрицы. MediaMTX выбросить их не может: он
# ремуксер и видео не декодирует. Этот сервис декодирует закрытый сегмент,
# прогоняет его через фильтр ffmpeg `mpdecimate`, который выбрасывает кадры,
# практически не отличающиеся от предыдущего, и кладёт результат на место
# оригинала.
#
# Замер на синтетике 1280x720@25, 60 с, видео без звука:
#   неподвижная сцена       1500 кадров /  91 КБ → 1 кадр  /   1.8 КБ
#   слабый шум матрицы      1500 кадров / 12 МБ  → 1 кадр  /   1.9 КБ
#   сильный шум             1500 кадров / 11 МБ  → 114 кадров / 19 МБ (!)
#   непрерывное движение    1500 кадров          → 1500 кадров
# Третья строка — причина, по которой TACITUS_OPT_MIN_GAIN_PERCENT существует и
# не может быть выключён: на шумном источнике перекодировка честно сохраняет
# шум и файл РАСТЁТ. Такой результат обязан быть отброшен, а оригинал — остаться
# нетронутым.
#
# Скрипт исполняется в образе bluenviron/mediamtx:1.15.1-ffmpeg, где есть
# только busybox ash. Ни bash, ни python в образе нет, а доустанавливать
# что-либо в рантайме проект запрещает (tests/test_compose_pinning.py). Поэтому
# здесь нет ни `[[`, ни `local`, ни `pipefail` — ровно как в
# scripts/simulate-stream-entrypoint.sh.
#
# Коды выхода:
#   0 — остановлен по сигналу
#   2 — окружение непригодно (нет каталога записей, неверная настройка)

set -eu

ENABLED="${TACITUS_OPT_ENABLED:-false}"
ROOT="${TACITUS_OPT_RECORDINGS_PATH:-/recordings}"
INTERVAL_SECONDS="${TACITUS_OPT_INTERVAL_SECONDS:-600}"
MIN_IDLE_SECONDS="${TACITUS_OPT_MIN_IDLE_SECONDS:-300}"
CRF="${TACITUS_OPT_CRF:-18}"
PRESET="${TACITUS_OPT_PRESET:-slow}"
HI="${TACITUS_OPT_HI:-768}"
LO="${TACITUS_OPT_LO:-320}"
FRAC="${TACITUS_OPT_FRAC:-0.33}"
MIN_GAIN_PERCENT="${TACITUS_OPT_MIN_GAIN_PERCENT:-10}"
NICE="${TACITUS_OPT_NICE:-10}"

# Тег в контейнере — единственный признак «этот сегмент уже обработан».
# Файл-маркер рядом с записью не годится: mediamtx-archive-manager.py
# выгружает в S3 всё, что найдёт обходом каталога, и маркеры уехали бы туда
# мусором. Тег переживает и перезапуск сервиса, и переезд файла.
readonly MARKER_TAG=tacitus_decimated

# Допуск на расхождение длительности до и после. Прореживание обязано сохранять
# таймлинию: часовая запись остаётся часовой, а не превращается в таймлапс
# (за это отвечает -fps_mode vfr, сохраняющий исходные PTS выживших кадров).
# Секунда — это хвост последнего фрагмента fmp4, а не смещение времени.
readonly MAX_DURATION_DRIFT_SECONDS=1

# Суффикс временного файла. Начинается с точки и заканчивается на .tmp:
# list_files() архиватора пропускает и то, и другое, поэтому недоделанный
# транскод не может уехать в S3, даже если оба сервиса работают одновременно.
readonly TMP_SUFFIX=.tacitus.tmp

FFMPEG_PID=""

timestamp() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }

log() { printf '%s optimizer %s\n' "$(timestamp)" "$*"; }

die() { printf '%s optimizer error: %s\n' "$(timestamp)" "$*" >&2; exit 2; }

# Целое число в разумных границах. Мусор в переменной окружения обязан
# останавливать сервис здесь, а не превращаться в аргумент ffmpeg.
require_int() {
    case "$2" in
        ''|*[!0-9]*) die "$1 должно быть целым числом, получено '$2'" ;;
    esac
    [ "$2" -ge "$3" ] && [ "$2" -le "$4" ] || die "$1=$2 вне диапазона $3..$4"
}

file_size() { stat -c %s "$1"; }

file_mtime() { stat -c %Y "$1"; }

# Число видеокадров. Считается по пакетам, а не декодированием
# (-count_frames): для часового сегмента разница между чтением индекса и полным
# декодированием — секунды против минут, а для видеопотока пакет и есть кадр.
frame_count() {
    ffprobe -v error -select_streams v:0 -count_packets \
        -show_entries stream=nb_read_packets -of csv=p=0 "$1" 2>/dev/null || true
}

duration_seconds() {
    ffprobe -v error -show_entries format=duration -of csv=p=0 "$1" 2>/dev/null || true
}

# Сегмент, который MediaMTX дописывает прямо сейчас, трогать нельзя:
# recordPartDuration равен 1 с, то есть активный файл меняется каждую секунду.
is_active_segment() {
    age=$(( $(date -u '+%s') - $(file_mtime "$1") ))
    [ "$age" -lt "$MIN_IDLE_SECONDS" ]
}

is_optimized() {
    tag=$(ffprobe -v error -show_entries "format_tags=$MARKER_TAG" \
        -of csv=p=0 "$1" 2>/dev/null || true)
    [ -n "$tag" ]
}

# Один прогон ffmpeg. Вынесен в функцию ради $! : обработка часового сегмента
# длится минутами, и SIGTERM обязан её прерывать, а не ждать.
run_ffmpeg() {
    nice -n "$NICE" ffmpeg -nostdin -hide_banner -loglevel error -y \
        -i "$1" -map 0 \
        -vf "mpdecimate=hi=$HI:lo=$LO:frac=$FRAC" -fps_mode:v vfr \
        -c:v libx264 -preset "$PRESET" -crf "$CRF" \
        -c:a copy \
        -metadata "$MARKER_TAG=1" \
        -movflags +frag_keyframe+empty_moov+default_base_moof+use_metadata_tags \
        -f mp4 "$2" &
    FFMPEG_PID=$!
    rc=0
    wait "$FFMPEG_PID" || rc=$?
    FFMPEG_PID=""
    return "$rc"
}

# Результат подменяет оригинал ТОЛЬКО пройдя все проверки разом. При любом
# сомнении временный файл удаляется, а оригинал остаётся нетронутым:
# перекодировка необратима, и запись, которой уже нет, не восстановить.
optimize_one() {
    src="$1"
    tmp="$(dirname "$src")/.$(basename "$src")$TMP_SUFFIX"

    src_bytes=$(file_size "$src")
    src_frames=$(frame_count "$src")
    src_duration=$(duration_seconds "$src")
    started=$(date -u '+%s')

    rc=0
    run_ffmpeg "$src" "$tmp" || rc=$?
    if [ "$rc" -ne 0 ]; then
        rm -f "$tmp"
        log "$src: ffmpeg завершился с кодом $rc, оригинал не тронут"
        return 0
    fi

    elapsed=$(( $(date -u '+%s') - started ))
    out_bytes=$(file_size "$tmp")
    out_frames=$(frame_count "$tmp")
    out_duration=$(duration_seconds "$tmp")

    if [ -z "$out_frames" ] || [ "$out_frames" -eq 0 ]; then
        rm -f "$tmp"
        log "$src: в результате нет видеокадров, оригинал не тронут"
        return 0
    fi

    if ! awk -v a="$src_duration" -v b="$out_duration" -v m="$MAX_DURATION_DRIFT_SECONDS" \
        'BEGIN { d = a - b; if (d < 0) d = -d; exit !(a > 0 && b > 0 && d <= m) }'; then
        rm -f "$tmp"
        log "$src: длительность поехала ($src_duration → $out_duration), оригинал не тронут"
        return 0
    fi

    # Предохранитель из шапки файла: на шумном источнике перекодировка честно
    # сохраняет шум, и файл становится больше исходного.
    if ! awk -v s="$src_bytes" -v o="$out_bytes" -v g="$MIN_GAIN_PERCENT" \
        'BEGIN { exit !(o <= s * (100 - g) / 100) }'; then
        rm -f "$tmp"
        log "$src: экономия меньше ${MIN_GAIN_PERCENT}% ($src_bytes → $out_bytes байт), оригинал не тронут"
        return 0
    fi

    # Возраст файла — то, по чему mediamtx-archive-manager.py решает, пора ли
    # выгружать сегмент в S3. Без переноса mtime обработка молодила бы каждую
    # запись, и архив не уезжал бы никогда.
    touch -r "$src" "$tmp"

    # Архиватор мог забрать и удалить сегмент, пока шёл транскод. Тогда
    # подменять нечего: результат выбрасывается, чтобы не воскресить в каталоге
    # файл, который уже лежит в S3, и не выгрузить его вторым объектом.
    if [ ! -f "$src" ]; then
        rm -f "$tmp"
        log "$src: исчез во время обработки (выгружен в архив), результат отброшен"
        return 0
    fi

    mv "$tmp" "$src"
    log "$src: кадров $src_frames → $out_frames, байт $src_bytes → $out_bytes, ${elapsed}с"
}

# Один обход каталога записей.
sweep() {
    processed=0
    for src in "$ROOT"/*/*.mp4; do
        [ -f "$src" ] || continue
        case "$src" in
            */.*) continue ;;
        esac
        is_active_segment "$src" && continue
        is_optimized "$src" && continue
        optimize_one "$src"
        processed=$((processed + 1))
    done
    [ "$processed" -eq 0 ] || log "обход завершён, обработано сегментов: $processed"
}

# --- preflight --------------------------------------------------------------

require_int TACITUS_OPT_INTERVAL_SECONDS "$INTERVAL_SECONDS" 10 86400
require_int TACITUS_OPT_MIN_IDLE_SECONDS "$MIN_IDLE_SECONDS" 10 86400
require_int TACITUS_OPT_CRF "$CRF" 0 51
require_int TACITUS_OPT_HI "$HI" 0 65535
require_int TACITUS_OPT_LO "$LO" 0 65535
require_int TACITUS_OPT_MIN_GAIN_PERCENT "$MIN_GAIN_PERCENT" 1 99
require_int TACITUS_OPT_NICE "$NICE" 0 19

case "$PRESET" in
    ultrafast|superfast|veryfast|faster|fast|medium|slow|slower|veryslow) ;;
    *) die "неизвестный TACITUS_OPT_PRESET '$PRESET'" ;;
esac

case "$FRAC" in
    ''|*[!0-9.]*) die "TACITUS_OPT_FRAC должно быть числом, получено '$FRAC'" ;;
esac

trap 'log "остановка"; [ -n "$FFMPEG_PID" ] && kill "$FFMPEG_PID" 2>/dev/null; exit 0' TERM INT

# Выключенный сервис обязан оставаться живым и молчаливым: контейнер объявлен
# в compose без профиля, поднимается всегда, и падать в рестарт-петлю из-за
# того, что оператор его не включил, он не должен.
if [ "$ENABLED" != "true" ]; then
    log "выключен (TACITUS_OPT_ENABLED=$ENABLED), записи не обрабатываются"
    while :; do sleep "$INTERVAL_SECONDS"; done
fi

[ -d "$ROOT" ] || die "каталог записей не существует: $ROOT"

log "включён: $ROOT, mpdecimate hi=$HI lo=$LO frac=$FRAC, x264 crf=$CRF preset=$PRESET, порог экономии ${MIN_GAIN_PERCENT}%"

# Временные файлы прошлого запуска: контейнер могли убить посреди транскода.
find "$ROOT" -name "*$TMP_SUFFIX" -type f -exec rm -f {} + 2>/dev/null || true

while :; do
    sweep
    sleep "$INTERVAL_SECONDS"
done
