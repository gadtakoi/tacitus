#!/usr/bin/env bash
#
# Проверяет, корректно ли развёрнут Tacitus на сервере.
#
#   scripts/healthcheck.sh                  # запуск на самом сервере
#   scripts/healthcheck.sh 1.2.3.4          # с рабочей машины: скрипт уезжает по ssh
#   scripts/healthcheck.sh 1.2.3.4 --user ubuntu --ssh-key ~/.ssh/id_ed25519
#
# Скрипт только читает состояние: ничего не устанавливает, не перезапускает и
# не меняет конфигурацию. Значения секретов не выводятся — проверяются только
# наличие файлов, права на них и то, что дефолтные значения переопределены.
#
# Коды выхода:
#   0 — критичных проблем нет (предупреждения возможны)
#   1 — есть FAIL (или WARN при --strict)
#   2 — проверку не удалось выполнить (нет ssh, неверные аргументы)

set -uo pipefail

readonly SELF="${BASH_SOURCE[0]:-}"
readonly DEFAULT_DEPLOY_DIR="/opt/tacitus"
readonly SECRETS_FILE="/etc/tacitus/deploy-secrets.yml"

# Контейнеры, которые должны быть запущены постоянно. Второе поле — есть ли у
# сервиса healthcheck в docker-compose.yml: у nginx и mediamtx-cleanup его нет
# осознанно, спрашивать с них состояние здоровья бессмысленно.
readonly LONG_RUNNING_CONTAINERS="
tacitus-app:health
tacitus-nginx:nohealth
tacitus-postgres:health
tacitus-redis:health
tacitus-minio:health
tacitus-mediamtx:health
tacitus-mediamtx-cleanup:nohealth
tacitus-recording-optimizer:nohealth
"

# Контейнеры-однократки: отработали и завершились с кодом 0.
readonly ONESHOT_CONTAINERS="
tacitus-db-migrations
tacitus-minio-init
"

die() {
    printf 'error: %s\n' "$*" >&2
    exit 2
}

usage() {
    cat <<'EOF'
Использование: scripts/healthcheck.sh [host] [опции]

  [host]                IP или домен сервера. Без него проверяется локальный хост.

Опции:
  --user USER           SSH-пользователь (по умолчанию root)
  --port PORT           SSH-порт (по умолчанию 22)
  --ssh-key PATH        Приватный ключ для подключения
  --dir PATH            Каталог деплоя (по умолчанию /opt/tacitus)
  --domain DOMAIN       Домен для проверки HTTPS (по умолчанию определяется сам)
  --strict              Считать предупреждения ошибками (код выхода 1)
  -h, --help            Показать эту справку

Примеры:
  scripts/healthcheck.sh 1.2.3.4
  scripts/healthcheck.sh 1.2.3.4 --user ubuntu --ssh-key ~/.ssh/id_ed25519
  ssh root@1.2.3.4 'bash -s' < scripts/healthcheck.sh
EOF
}

host=""
ssh_user="root"
ssh_port="22"
ssh_key=""
deploy_dir="$DEFAULT_DEPLOY_DIR"
domain=""
strict=false

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --user)    ssh_user="${2:?--user требует значение}"; shift 2 ;;
        --port)    ssh_port="${2:?--port требует значение}"; shift 2 ;;
        --ssh-key) ssh_key="${2:?--ssh-key требует значение}"; shift 2 ;;
        --dir)     deploy_dir="${2:?--dir требует значение}"; shift 2 ;;
        --domain)  domain="${2:?--domain требует значение}"; shift 2 ;;
        --strict)  strict=true; shift ;;
        -*)        die "неизвестная опция: $1 (см. --help)" ;;
        *)
            [ -z "$host" ] || die "хост указан дважды: '$host' и '$1'"
            host="$1"; shift ;;
    esac
done

# --- удалённый режим --------------------------------------------------------
#
# Скрипт самодостаточен, поэтому уезжает на сервер через stdin: ничего
# копировать и потом удалять на хосте не нужно.

if [ -n "$host" ]; then
    command -v ssh >/dev/null || die "нужен ssh"
    [ -n "$SELF" ] || die "скрипт передан через stdin — для удалённого режима запустите его как файл"
    [ -r "$SELF" ] || die "не удалось прочитать сам скрипт ($SELF) для передачи на сервер"

    remote_args=(--dir "$deploy_dir")
    [ -n "$domain" ] && remote_args+=(--domain "$domain")
    [ "$strict" = true ] && remote_args+=(--strict)
    printf -v remote_argv '%q ' "${remote_args[@]}"

    ssh_opts=(-p "$ssh_port" -o StrictHostKeyChecking=accept-new)
    [ -n "$ssh_key" ] && ssh_opts+=(-i "$ssh_key")

    exec ssh "${ssh_opts[@]}" "$ssh_user@$host" "bash -s -- $remote_argv" < "$SELF"
fi

# --- вывод ------------------------------------------------------------------

if [ -t 1 ]; then
    C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_FAIL=$'\033[31m'
    C_DIM=$'\033[2m'; C_BOLD=$'\033[1m'; C_OFF=$'\033[0m'
else
    C_OK=""; C_WARN=""; C_FAIL=""; C_DIM=""; C_BOLD=""; C_OFF=""
fi

pass_count=0
warn_count=0
fail_count=0

section() { printf '\n%s%s%s\n' "$C_BOLD" "$*" "$C_OFF"; }
ok()      { pass_count=$((pass_count + 1)); printf '  %s[ OK ]%s %s\n' "$C_OK" "$C_OFF" "$*"; }
warn()    { warn_count=$((warn_count + 1)); printf '  %s[WARN]%s %s\n' "$C_WARN" "$C_OFF" "$*"; }
bad()     { fail_count=$((fail_count + 1)); printf '  %s[FAIL]%s %s\n' "$C_FAIL" "$C_OFF" "$*"; }
note()    { printf '  %s       %s%s\n' "$C_DIM" "$*" "$C_OFF"; }

# --- права ------------------------------------------------------------------
#
# Скрипт не требует root, но без него часть проверок недоступна: .env и файл
# секретов имеют режим 0600, docker и ufw обычно тоже требуют привилегий.
# Вместо `exec sudo` используется префикс — так скрипт одинаково работает и
# запущенный файлом, и переданный в `bash -s` через stdin.

sudo_prefix=()
have_root=true
if [ "$(id -u)" -ne 0 ]; then
    if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
        sudo_prefix=(sudo -n)
    else
        have_root=false
    fi
fi

r() { ${sudo_prefix[@]+"${sudo_prefix[@]}"} "$@"; }
d() { r docker "$@"; }

printf '%sПроверка состояния Tacitus%s  (%s, %s)\n' \
    "$C_BOLD" "$C_OFF" "$(hostname)" "$(date -Is 2>/dev/null || date)"
if [ "$have_root" = false ]; then
    printf '%sНет root и беспарольного sudo — часть проверок будет пропущена.%s\n' \
        "$C_WARN" "$C_OFF"
fi

# --- 1. Хост ----------------------------------------------------------------

section "1. Хост"

if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    os_name="$(. /etc/os-release 2>/dev/null && printf '%s' "${PRETTY_NAME:-${ID:-unknown}}")"
    os_like="$(. /etc/os-release 2>/dev/null && printf '%s' "${ID_LIKE:-${ID:-}}")"
    case "$os_like" in
        *debian*|*ubuntu*) ok "ОС: $os_name" ;;
        *) warn "ОС: $os_name — плейбук и роли рассчитаны на Debian/Ubuntu" ;;
    esac
else
    warn "не удалось определить ОС: нет /etc/os-release"
fi

disk_line="$(df -P "$deploy_dir" 2>/dev/null | awk 'NR==2' || true)"
[ -n "$disk_line" ] || disk_line="$(df -P / | awk 'NR==2')"
disk_pct="$(printf '%s\n' "$disk_line" | awk '{gsub("%","",$5); print $5}')"
disk_avail_mb="$(printf '%s\n' "$disk_line" | awk '{print int($4/1024)}')"
if [ -n "$disk_pct" ] && [ "$disk_pct" -eq "$disk_pct" ] 2>/dev/null; then
    if [ "$disk_pct" -ge 90 ]; then
        bad "диск заполнен на ${disk_pct}% (свободно ${disk_avail_mb} МиБ) — записи и логи скоро остановят сервисы"
    elif [ "$disk_pct" -ge 80 ]; then
        warn "диск заполнен на ${disk_pct}% (свободно ${disk_avail_mb} МиБ)"
    else
        ok "диск: занято ${disk_pct}%, свободно ${disk_avail_mb} МиБ"
    fi
fi

ram_kb="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
swap_kb="$(awk '/^SwapTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
if [ "${ram_kb:-0}" -gt 0 ]; then
    if [ "$ram_kb" -lt 2000000 ] && [ "${swap_kb:-0}" -eq 0 ]; then
        warn "RAM $((ram_kb / 1024)) МиБ без swap — сборка фронтенда упрётся в OOM"
    else
        ok "память: RAM $((ram_kb / 1024)) МиБ, swap $((swap_kb / 1024)) МиБ"
    fi
fi

if [ -e /var/run/reboot-required ]; then
    warn "требуется перезагрузка хоста (обновлялось ядро или системные библиотеки)"
else
    ok "перезагрузка хоста не требуется"
fi

# --- 2. Docker --------------------------------------------------------------

section "2. Docker"

docker_ready=false
if command -v docker >/dev/null 2>&1; then
    ok "docker установлен: $(docker --version 2>/dev/null)"

    if d compose version >/dev/null 2>&1; then
        ok "плагин docker compose: $(d compose version --short 2>/dev/null)"
    else
        bad "нет плагина docker compose — стек нечем поднимать"
    fi

    if d info >/dev/null 2>&1; then
        ok "docker daemon отвечает"
        docker_ready=true
    elif [ "$have_root" = false ]; then
        bad "docker daemon недоступен — вероятно, не хватает прав (запустите под root)"
    else
        bad "docker daemon недоступен — проверьте systemctl status docker"
    fi

    if systemctl is-enabled docker >/dev/null 2>&1; then
        ok "docker включён в автозапуск"
    else
        warn "docker не в автозапуске — стек не поднимется после перезагрузки"
    fi
else
    bad "docker не установлен — роль docker_install не отработала"
fi

# --- 3. Файлы деплоя --------------------------------------------------------

section "3. Файлы деплоя ($deploy_dir)"

env_file=""
env_readable=false
if r test -d "$deploy_dir"; then
    ok "каталог деплоя существует"

    for f in docker-compose.yml Dockerfile .env nginx/nginx.conf alembic.ini \
             requirements.txt app migrations frontend scripts; do
        if r test -e "$deploy_dir/$f"; then
            ok "есть $f"
        else
            bad "нет $deploy_dir/$f — деплой прошёл не полностью"
        fi
    done

    if r test -f "$deploy_dir/.env"; then
        env_file="$deploy_dir/.env"
        # test -f отрабатывает и без прав на чтение самого файла (режим 0600 на
        # каталоге 0755), поэтому читаемость проверяется отдельно — иначе
        # проверки ниже приняли бы «не смог прочитать» за «переменная не задана».
        r test -r "$env_file" && env_readable=true
        env_mode="$(r stat -c '%a' "$env_file" 2>/dev/null)"
        if [ "$env_mode" = "600" ]; then
            ok ".env с правами 600"
        else
            bad ".env с правами ${env_mode:-?} — в файле лежат секреты, ожидается 600"
        fi
    fi
else
    bad "нет каталога $deploy_dir — приложение не разворачивалось"
fi

if r test -f "$SECRETS_FILE"; then
    secrets_mode="$(r stat -c '%a' "$SECRETS_FILE" 2>/dev/null)"
    if [ "$secrets_mode" = "600" ]; then
        ok "файл секретов $SECRETS_FILE с правами 600"
    else
        bad "файл секретов $SECRETS_FILE с правами ${secrets_mode:-?}, ожидается 600"
    fi
else
    note "$SECRETS_FILE отсутствует — секреты передавались через -e (это допустимо)"
fi

# --- 4. Конфигурация приложения ---------------------------------------------
#
# Значения переменных нигде не печатаются: сравнение идёт с известными
# дефолтами, наружу выходит только имя переменной и вердикт.

section "4. Конфигурация (.env)"

env_get() {
    [ "$env_readable" = true ] || return 1
    r sed -n "s/^$1=//p" "$env_file" 2>/dev/null | tail -n1
}

check_not_default() {  # имя переменной, дефолтное значение
    local value
    value="$(env_get "$1")"
    if [ -z "$value" ]; then
        bad "$1 не задан"
    elif [ "$value" = "$2" ]; then
        bad "$1 оставлен дефолтным"
    else
        ok "$1 переопределён"
    fi
}

if [ "$env_readable" = true ]; then
    environment="$(env_get TACITUS_ENVIRONMENT)"
    if [ "$environment" = "production" ]; then
        ok "TACITUS_ENVIRONMENT=production"
    else
        warn "TACITUS_ENVIRONMENT=${environment:-не задан} — на сервере ожидается production"
    fi

    if [ "$(env_get TACITUS_DEBUG)" = "true" ]; then
        bad "TACITUS_DEBUG=true — отладочный режим на сервере"
    else
        ok "TACITUS_DEBUG выключен"
    fi

    jwt="$(env_get TACITUS_JWT_SECRET)"
    if [ -z "$jwt" ]; then
        bad "TACITUS_JWT_SECRET не задан"
    elif [ "$jwt" = "change-me-in-production" ]; then
        bad "TACITUS_JWT_SECRET оставлен дефолтным"
    elif [ "${#jwt}" -lt 32 ]; then
        bad "TACITUS_JWT_SECRET короче 32 символов — приложение не стартует в production"
    else
        ok "TACITUS_JWT_SECRET переопределён и достаточной длины"
    fi
    unset jwt

    check_not_default S3_ACCESS_KEY minioadmin
    check_not_default S3_SECRET_KEY minioadmin
    check_not_default POSTGRES_PASSWORD change-me-in-production
    check_not_default TACITUS_MTX_API_PASSWORD dev-api-change-me

    cors="$(env_get TACITUS_CORS_ORIGINS)"
    case "$cors" in
        "")    bad "TACITUS_CORS_ORIGINS не задан" ;;
        *"*"*) bad "TACITUS_CORS_ORIGINS содержит '*' — приложение не стартует в production" ;;
        *)     ok "TACITUS_CORS_ORIGINS задан без wildcard" ;;
    esac
    unset cors
elif [ -n "$env_file" ]; then
    warn "проверка .env пропущена: нет прав на чтение файла (запустите под root или через sudo)"
else
    warn "проверка .env пропущена: файл не найден"
fi

# --- 5. Контейнеры ----------------------------------------------------------

section "5. Контейнеры"

if [ "$docker_ready" = true ]; then
    for entry in $LONG_RUNNING_CONTAINERS; do
        name="${entry%%:*}"
        kind="${entry##*:}"

        state="$(d inspect -f '{{.State.Status}}' "$name" 2>/dev/null)"
        if [ -z "$state" ]; then
            bad "$name: контейнер не создан"
            continue
        fi

        if [ "$state" != "running" ]; then
            bad "$name: состояние '$state' вместо running"
            continue
        fi

        restarts="$(d inspect -f '{{.RestartCount}}' "$name" 2>/dev/null)"
        restarts="${restarts:-0}"
        health="$(d inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$name" 2>/dev/null)"

        if [ "$kind" = "health" ] && [ "$health" = "starting" ]; then
            warn "$name: запущен, healthcheck ещё не прошёл (starting)"
        elif [ "$kind" = "health" ] && [ "$health" != "healthy" ]; then
            bad "$name: запущен, но healthcheck в состоянии '$health'"
        elif [ "$restarts" -gt 5 ]; then
            warn "$name: запущен, но перезапускался $restarts раз — смотрите docker logs $name"
        else
            ok "$name: running"
        fi
    done

    for name in $ONESHOT_CONTAINERS; do
        state="$(d inspect -f '{{.State.Status}}' "$name" 2>/dev/null)"
        if [ -z "$state" ]; then
            bad "$name: контейнер не создан — миграции или инициализация бакета не выполнялись"
            continue
        fi

        exit_code="$(d inspect -f '{{.State.ExitCode}}' "$name" 2>/dev/null)"
        if [ "$state" = "running" ]; then
            warn "$name: всё ещё выполняется"
        elif [ "$exit_code" = "0" ]; then
            ok "$name: отработал успешно (exit 0)"
        else
            bad "$name: завершился с кодом ${exit_code:-?} — смотрите docker logs $name"
        fi
    done

    # Имитатор потока — сервис под профилем compose `simulator`, на сервере его
    # быть не должно: он держит действующие учётные данные публикатора и
    # наполняет recordings синтетическим видео. Наличие контейнера означает,
    # что профиль включили в production вручную.
    if [ -n "$(d ps -aq -f name=tacitus-stream-simulator 2>/dev/null)" ]; then
        bad "tacitus-stream-simulator существует на сервере — профиль simulator включён в production"
    else
        ok "имитатор потока не развёрнут (профиль simulator выключен)"
    fi
else
    warn "проверка контейнеров пропущена: docker недоступен"
fi

# --- 6. Приложение ----------------------------------------------------------

section "6. Приложение"

http_get() {
    if command -v curl >/dev/null 2>&1; then
        curl --silent --show-error --max-time 10 "$1"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- --timeout=10 "$1"
    else
        return 127
    fi
}

http_port="$(env_get NGINX_HOST_PORT)"
http_port="${http_port:-80}"
health_url="http://localhost:${http_port}/health"

body="$(http_get "$health_url" 2>/dev/null)"
if [ $? -eq 127 ]; then
    warn "нет ни curl, ни wget — HTTP-проверки пропущены"
elif [ "$body" = '{"status":"ok"}' ]; then
    ok "$health_url отвечает ok"
else
    bad "$health_url не отвечает ok (получено: ${body:-нет ответа})"
fi
unset body

# --- 7. HTTPS и сертификат --------------------------------------------------

section "7. HTTPS"

if [ -z "$domain" ]; then
    domain="$(r ls /etc/letsencrypt/live 2>/dev/null | grep -v README | head -n1)"
fi

if [ -z "$domain" ]; then
    note "сертификатов Let's Encrypt нет — развёрнуто по HTTP, проверки HTTPS пропущены"
else
    cert="/etc/letsencrypt/live/$domain/fullchain.pem"
    if r test -f "$cert"; then
        end_date="$(r openssl x509 -in "$cert" -noout -enddate 2>/dev/null | cut -d= -f2)"
        end_ts="$(date -d "$end_date" +%s 2>/dev/null)"
        if [ -n "$end_ts" ]; then
            days_left=$(( (end_ts - $(date +%s)) / 86400 ))
            if [ "$days_left" -lt 0 ]; then
                bad "сертификат $domain истёк $((-days_left)) дн. назад"
            elif [ "$days_left" -lt 10 ]; then
                bad "сертификат $domain истекает через $days_left дн. — автообновление не сработало"
            elif [ "$days_left" -lt 21 ]; then
                warn "сертификат $domain истекает через $days_left дн."
            else
                ok "сертификат $domain действителен ещё $days_left дн."
            fi
        else
            warn "не удалось разобрать срок действия сертификата $domain"
        fi

        if r openssl x509 -in "$cert" -noout -issuer 2>/dev/null | grep -qi staging; then
            warn "сертификат выпущен staging-CA Let's Encrypt — браузеры ему не доверяют"
        fi
    else
        bad "нет $cert, хотя каталог сертификата существует"
    fi

    if r crontab -l 2>/dev/null | grep -q 'certbot renew'; then
        ok "автообновление сертификата настроено (cron)"
    elif systemctl list-timers --all 2>/dev/null | grep -q certbot; then
        ok "автообновление сертификата настроено (systemd timer)"
    else
        bad "не найдено автообновление сертификата — через 90 дней HTTPS перестанет работать"
    fi

    if command -v curl >/dev/null 2>&1; then
        # --resolve, чтобы проверка шла именно в этот сервер, даже если DNS
        # ещё указывает на старый адрес.
        redirect_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
            --resolve "$domain:80:127.0.0.1" "http://$domain/" 2>/dev/null)"
        case "$redirect_code" in
            301|308) ok "HTTP редиректит на HTTPS ($redirect_code)" ;;
            *)       warn "HTTP не редиректит на HTTPS (код ${redirect_code:-нет ответа})" ;;
        esac

        https_body="$(curl -sk --max-time 10 --resolve "$domain:443:127.0.0.1" \
            "https://$domain/health" 2>/dev/null)"
        if [ "$https_body" = '{"status":"ok"}' ]; then
            ok "https://$domain/health отвечает ok"
        else
            bad "https://$domain/health не отвечает ok (получено: ${https_body:-нет ответа})"
        fi
        unset https_body
    else
        warn "нет curl — HTTPS-запросы не проверены"
    fi
fi

# --- 8. Сеть и firewall -----------------------------------------------------

section "8. Сеть и firewall"

if command -v ufw >/dev/null 2>&1; then
    ufw_status="$(r ufw status verbose 2>/dev/null)"
    if printf '%s' "$ufw_status" | grep -q 'Status: active'; then
        ok "ufw активен"

        if printf '%s' "$ufw_status" | grep -q 'Default: deny (incoming)'; then
            ok "политика по умолчанию: deny incoming"
        else
            warn "политика по умолчанию не deny incoming — хост принимает всё подряд"
        fi

        for port in 80 443; do
            if printf '%s' "$ufw_status" | grep -qE "^${port}(/tcp)?[[:space:]]"; then
                ok "порт $port открыт"
            else
                warn "порт $port не открыт в ufw — приложение недоступно снаружи"
            fi
        done

        if printf '%s' "$ufw_status" | grep -qiE "^(22(/tcp)?[[:space:]]|.*ssh)"; then
            ok "SSH разрешён в ufw"
        else
            warn "не видно правила ufw для SSH — следующий деплой рискует потерять доступ"
        fi
    else
        warn "ufw установлен, но не активен (деплой с firewall_enabled=false?)"
    fi
else
    warn "ufw не установлен — хост полагается только на firewall провайдера"
fi

# Docker публикует порты в обход ufw, поэтому 0.0.0.0 на порту БД или RTSP
# означает реальную доступность из интернета независимо от правил firewall.
if command -v ss >/dev/null 2>&1; then
    listen="$(r ss -tlnH 2>/dev/null)"
    pg_port="$(env_get POSTGRES_HOST_PORT)"
    rtsp_port="$(env_get TACITUS_MTX_RTSP_PORT)"

    for entry in "PostgreSQL:${pg_port:-15432}" "RTSP:${rtsp_port:-8554}"; do
        label="${entry%%:*}"
        port="${entry##*:}"
        if printf '%s\n' "$listen" | awk '{print $4}' | grep -qE "^(0\.0\.0\.0|\*|\[::\]):${port}$"; then
            warn "$label слушает на всех интерфейсах (порт $port) — публикуется в сеть в обход ufw"
        else
            ok "$label не опубликован наружу (порт $port только на loopback или закрыт)"
        fi
    done
else
    note "нет ss — проверка опубликованных портов пропущена"
fi

# --- 9. Автообновления безопасности -----------------------------------------

section "9. Автообновления и пакеты ОС"

if command -v dpkg >/dev/null 2>&1 && dpkg -s unattended-upgrades >/dev/null 2>&1; then
    ok "пакет unattended-upgrades установлен"

    auto_conf="/etc/apt/apt.conf.d/20auto-upgrades"
    if r grep -q 'Unattended-Upgrade "1"' "$auto_conf" 2>/dev/null; then
        ok "ежедневная установка обновлений включена ($auto_conf)"
    else
        warn "в $auto_conf не включён Unattended-Upgrade — патчи не ставятся автоматически"
    fi

    if systemctl is-enabled unattended-upgrades >/dev/null 2>&1; then
        ok "сервис unattended-upgrades включён"
    else
        warn "сервис unattended-upgrades выключен"
    fi

    last_log="/var/log/unattended-upgrades/unattended-upgrades.log"
    if r test -f "$last_log"; then
        last_run="$(r stat -c '%Y' "$last_log" 2>/dev/null)"
        if [ -n "$last_run" ]; then
            days_ago=$(( ($(date +%s) - last_run) / 86400 ))
            if [ "$days_ago" -gt 7 ]; then
                warn "последний запуск автообновления был $days_ago дн. назад"
            else
                ok "автообновление отрабатывало $days_ago дн. назад"
            fi
        fi
    else
        note "лога автообновлений ещё нет — сервис не отрабатывал ни разу"
    fi
else
    warn "unattended-upgrades не установлен — security-патчи ставятся только вручную"
fi

# Симуляция (-s), никаких изменений: показывает, сколько обновлений ждёт.
if command -v apt-get >/dev/null 2>&1; then
    apt_sim="$(r apt-get -s -q -o Debug::NoLocking=1 upgrade 2>/dev/null | grep '^Inst')"
    pending="$(printf '%s\n' "$apt_sim" | grep -c '^Inst')"
    security="$(printf '%s\n' "$apt_sim" | grep -ci security)"
    if [ "${security:-0}" -gt 0 ]; then
        warn "ждут установки $pending обновлений, из них security: $security — примените их деплоем с --update-os"
    elif [ "${pending:-0}" -gt 0 ]; then
        note "ждут установки $pending обновлений (не security)"
    else
        ok "все пакеты ОС актуальны"
    fi
    unset apt_sim
fi

# --- 10. Данные и логи ------------------------------------------------------

section "10. Данные и логи"

if [ "$docker_ready" = true ]; then
    # Имя проекта compose = имя каталога деплоя, отсюда префикс томов.
    project="$(basename "$deploy_dir")"
    for volume in postgres-data minio-data redis-data; do
        if d volume inspect "${project}_${volume}" >/dev/null 2>&1; then
            ok "том ${project}_${volume} существует"
        else
            bad "нет тома ${project}_${volume} — данные негде хранить"
        fi
    done
fi

if r test -d /var/log/tacitus; then
    ok "каталог логов /var/log/tacitus существует"
else
    warn "нет /var/log/tacitus"
fi

if r test -d "$deploy_dir/recordings"; then
    rec_owner="$(r stat -c '%u:%g' "$deploy_dir/recordings" 2>/dev/null)"
    if [ "$rec_owner" = "1000:1000" ]; then
        ok "каталог recordings/ с владельцем 1000:1000"
    else
        warn "каталог recordings/ с владельцем ${rec_owner:-?}, ожидается 1000:1000 — MediaMTX не сможет писать"
    fi
else
    warn "нет $deploy_dir/recordings — MediaMTX некуда писать записи"
fi

# --- Итог -------------------------------------------------------------------

printf '\n%sИтог:%s %s%d OK%s, %s%d предупреждений%s, %s%d ошибок%s\n' \
    "$C_BOLD" "$C_OFF" \
    "$C_OK" "$pass_count" "$C_OFF" \
    "$C_WARN" "$warn_count" "$C_OFF" \
    "$C_FAIL" "$fail_count" "$C_OFF"

if [ "$fail_count" -gt 0 ]; then
    printf '%sСервер настроен не полностью — разберите пункты FAIL выше.%s\n' "$C_FAIL" "$C_OFF"
    exit 1
fi

if [ "$warn_count" -gt 0 ] && [ "$strict" = true ]; then
    printf '%s--strict: предупреждения считаются ошибками.%s\n' "$C_WARN" "$C_OFF"
    exit 1
fi

printf '%sКритичных проблем не найдено.%s\n' "$C_OK" "$C_OFF"
exit 0
