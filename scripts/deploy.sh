#!/usr/bin/env bash
#
# Разворачивает Tacitus на указанном сервере за один запуск.
#
#   scripts/deploy.sh 1.2.3.4
#   scripts/deploy.sh 1.2.3.4 --domain example.com --email admin@example.com --https
#
# Вся работа идёт на целевом хосте: репозиторий передаётся одним SSH-соединением,
# Ansible ставится там же и запускается с локальным подключением. Поэтому на
# машине, с которой вы это набираете, не нужны ни Ansible, ни рабочий WSL —
# достаточно git, ssh и curl. Это единственный способ, который работает с
# Windows, где Ansible нативно не запускается.
#
# Секреты (JWT, S3, PostgreSQL, MediaMTX) генерируются на сервере при первом
# деплое и переиспользуются при последующих. Они не проходят через историю shell
# и не сохраняются на управляющей машине.
#
# Требования к серверу: Debian/Ubuntu, вход под root или пользователь с
# беспарольным sudo. Ключевой доступ по SSH избавит от ввода пароля.

set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

info() {
    printf '\033[1m==>\033[0m %s\n' "$*"
}

usage() {
    cat <<'EOF'
Использование: scripts/deploy.sh <host> [опции]

  <host>                IP или домен целевого сервера

Опции:
  --user USER           SSH-пользователь (по умолчанию root)
  --port PORT           SSH-порт (по умолчанию 22)
  --ssh-key PATH        Приватный ключ для подключения
  --domain DOMAIN       Домен приложения (нужен для HTTPS)
  --email EMAIL         Email для Let's Encrypt
  --https               Включить HTTPS и выпустить сертификат
  --staging             Использовать staging-сертификат Let's Encrypt
  --tags TAGS           Передать --tags в ansible-playbook (например app)
  --no-swap             Не создавать swap на малой машине
  -e, --var KEY=VALUE   Дополнительная переменная для плейбука (можно повторять)
  -h, --help            Показать эту справку

Примеры:
  scripts/deploy.sh 1.2.3.4
  scripts/deploy.sh 1.2.3.4 --user ubuntu --ssh-key ~/.ssh/id_ed25519
  scripts/deploy.sh 1.2.3.4 --domain example.com --email me@example.com --https --staging
  scripts/deploy.sh 1.2.3.4 --tags app
EOF
}

host=""
ssh_user="root"
ssh_port="22"
ssh_key=""
domain=""
email=""
enable_https=false
certbot_staging=false
tags=""
ensure_swap=true
extra_vars=()

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --user)    ssh_user="${2:?--user требует значение}"; shift 2 ;;
        --port)    ssh_port="${2:?--port требует значение}"; shift 2 ;;
        --ssh-key) ssh_key="${2:?--ssh-key требует значение}"; shift 2 ;;
        --domain)  domain="${2:?--domain требует значение}"; shift 2 ;;
        --email)   email="${2:?--email требует значение}"; shift 2 ;;
        --https)   enable_https=true; shift ;;
        --staging) certbot_staging=true; shift ;;
        --tags)    tags="${2:?--tags требует значение}"; shift 2 ;;
        --no-swap) ensure_swap=false; shift ;;
        -e|--var)  extra_vars+=("${2:?--var требует значение}"); shift 2 ;;
        -*)        die "неизвестная опция: $1 (см. --help)" ;;
        *)
            [ -z "$host" ] || die "хост указан дважды: '$host' и '$1'"
            host="$1"; shift ;;
    esac
done

# --- pre-checks -------------------------------------------------------------

[ -n "$host" ] || { usage >&2; die "не указан хост"; }
command -v git >/dev/null || die "нужен git"
command -v ssh >/dev/null || die "нужен ssh"

if [ "$enable_https" = true ] && [ -z "$domain" ]; then
    die "--https требует --domain: сертификат выпускается на доменное имя"
fi

git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1 \
    || die "$REPO_ROOT не является git-репозиторием"

# Деплоится HEAD, а не рабочая копия: так на сервере оказывается ровно то
# состояние, которое можно воспроизвести по коммиту.
if [ -n "$(git -C "$REPO_ROOT" status --porcelain)" ]; then
    printf 'warning: в рабочей копии есть незакоммиченные изменения — они НЕ попадут на сервер\n' >&2
fi

ssh_opts=(-p "$ssh_port" -o StrictHostKeyChecking=accept-new)
[ -n "$ssh_key" ] && ssh_opts+=(-i "$ssh_key")

# --- аргументы для скрипта на сервере ---------------------------------------

remote_args=(
    --public-host "$host"
    --deploy-user "$ssh_user"
)
[ -n "$domain" ] && remote_args+=(--domain "$domain")
[ -n "$email" ] && remote_args+=(--email "$email")
[ "$enable_https" = true ] && remote_args+=(--https)
[ "$certbot_staging" = true ] && remote_args+=(--staging)
[ "$ensure_swap" = false ] && remote_args+=(--no-swap)
[ -n "$tags" ] && remote_args+=(--tags "$tags")
for var in ${extra_vars+"${extra_vars[@]}"}; do
    remote_args+=(--var "$var")
done

# Аргументы уезжают в командную строку ssh, поэтому экранируем их как есть.
printf -v remote_argv '%q ' "${remote_args[@]}"

readonly REMOTE_DIR='$HOME/.tacitus-deploy'

# Один вход по SSH на весь деплой: архив приезжает через stdin, дальше сервер
# работает сам. Меньше соединений — меньше запросов пароля, если ключа нет.
remote_cmd="
set -eu
mkdir -p $REMOTE_DIR
cat > $REMOTE_DIR/src.tar.gz
rm -rf $REMOTE_DIR/src
mkdir -p $REMOTE_DIR/src
tar xzf $REMOTE_DIR/src.tar.gz -C $REMOTE_DIR/src
exec bash $REMOTE_DIR/src/scripts/deploy-remote.sh $remote_argv
"

info "Деплой $(git -C "$REPO_ROOT" rev-parse --short HEAD) на $ssh_user@$host"

git -C "$REPO_ROOT" archive --format=tar.gz HEAD \
    | ssh "${ssh_opts[@]}" "$ssh_user@$host" "$remote_cmd"

# --- post-check с управляющей машины ----------------------------------------
#
# Плейбук проверяет health-endpoint изнутри сервера. Здесь проверяем, что
# приложение видно снаружи — это ловит закрытый порт и firewall провайдера.

if [ "$enable_https" = true ]; then
    health_url="https://$domain/health"
else
    health_url="http://$host/health"
fi

if ! command -v curl >/dev/null; then
    info "Готово. curl не найден — проверьте вручную: $health_url"
    exit 0
fi

curl_opts=(--silent --show-error --max-time 20)
# Staging-сертификат Let's Encrypt подписан недоверенным CA — это ожидаемо.
[ "$certbot_staging" = true ] && curl_opts+=(--insecure)

info "Проверка $health_url"
if body="$(curl "${curl_opts[@]}" "$health_url")" && [ "$body" = '{"status":"ok"}' ]; then
    info "Готово: приложение отвечает на $health_url"
else
    die "приложение развёрнуто, но $health_url не отвечает ok (получено: ${body:-нет ответа})"
fi
