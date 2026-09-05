#!/usr/bin/env bash
#
# Выполняется НА целевом сервере. Руками не запускается — его загружает и
# вызывает scripts/deploy.sh.
#
# Задача: подготовить хост (Ansible, rsync, swap), обеспечить постоянные
# секреты и запустить ansible/playbook.yml с локальным подключением.

set -euo pipefail

readonly SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly SECRETS_DIR="/etc/tacitus"
readonly SECRETS_FILE="$SECRETS_DIR/deploy-secrets.yml"
readonly SWAP_FILE="/swapfile"
readonly SWAP_SIZE="2G"
# Ниже этого объёма сборка фронтенда в Docker не помещается в память и падает
# по OOM, если нет swap.
readonly MIN_RAM_KB=2000000

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

info() {
    printf '\033[1m-->\033[0m %s\n' "$*"
}

public_host=""
deploy_user="root"
domain=""
email=""
enable_https=false
certbot_staging=false
ensure_swap=true
tags=""
extra_vars=()

while [ $# -gt 0 ]; do
    case "$1" in
        --public-host) public_host="${2:?}"; shift 2 ;;
        --deploy-user) deploy_user="${2:?}"; shift 2 ;;
        --domain)      domain="${2:?}"; shift 2 ;;
        --email)       email="${2:?}"; shift 2 ;;
        --https)       enable_https=true; shift ;;
        --staging)     certbot_staging=true; shift ;;
        --no-swap)     ensure_swap=false; shift ;;
        --tags)        tags="${2:?}"; shift 2 ;;
        --var)         extra_vars+=("${2:?}"); shift 2 ;;
        *)             die "неизвестный аргумент: $1" ;;
    esac
done

# --- права ------------------------------------------------------------------

if [ "$(id -u)" -ne 0 ]; then
    command -v sudo >/dev/null || die "нужен root или sudo"
    sudo -n true 2>/dev/null \
        || die "нужен беспарольный sudo: добавьте '$(id -un) ALL=(ALL) NOPASSWD:ALL' в sudoers"
    exec sudo -n bash "${BASH_SOURCE[0]}" "$@"
fi

# --- pre-checks -------------------------------------------------------------

command -v apt-get >/dev/null \
    || die "поддерживаются только Debian/Ubuntu (роль docker_install работает через apt)"
command -v openssl >/dev/null || die "нужен openssl для генерации секретов"

# --- пакеты -----------------------------------------------------------------
#
# sshpass нужен даже при локальном подключении: ansible.posix.synchronize
# безусловно передаёт ansible_ssh_pass в модуль и требует бинарник, если
# значение непустое.

needed=()
command -v ansible-playbook >/dev/null || needed+=(ansible)
command -v rsync >/dev/null || needed+=(rsync)
command -v sshpass >/dev/null || needed+=(sshpass)

# Коллекция ansible.posix идёт в пакете ansible, а не в ansible-core.
if command -v ansible-galaxy >/dev/null \
    && ! ansible-galaxy collection list ansible.posix >/dev/null 2>&1; then
    needed+=(ansible)
fi

if [ ${#needed[@]} -gt 0 ]; then
    info "Установка: ${needed[*]}"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq "${needed[@]}"
fi

# --- swap -------------------------------------------------------------------

if [ "$ensure_swap" = true ]; then
    ram_kb="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
    swap_kb="$(awk '/^SwapTotal:/ {print $2}' /proc/meminfo)"

    if [ "$swap_kb" -eq 0 ] && [ "$ram_kb" -lt "$MIN_RAM_KB" ] && [ ! -e "$SWAP_FILE" ]; then
        info "RAM $((ram_kb / 1024)) МиБ без swap — создаю $SWAP_FILE на $SWAP_SIZE"
        fallocate -l "$SWAP_SIZE" "$SWAP_FILE"
        chmod 600 "$SWAP_FILE"
        mkswap -q "$SWAP_FILE"
        swapon "$SWAP_FILE"
        # Без записи в fstab swap исчезнет после перезагрузки, и следующая
        # сборка снова упрётся в OOM.
        grep -qF "$SWAP_FILE" /etc/fstab || printf '%s none swap sw 0 0\n' "$SWAP_FILE" >> /etc/fstab
    fi
fi

# --- секреты ----------------------------------------------------------------
#
# Генерируются один раз и переиспользуются: PostgreSQL применяет пароль только
# при инициализации тома, поэтому новый пароль на повторном деплое оставил бы
# приложение с credentials, которые база уже не принимает.

if [ ! -f "$SECRETS_FILE" ]; then
    info "Генерация секретов в $SECRETS_FILE"
    mkdir -p "$SECRETS_DIR"
    chmod 700 "$SECRETS_DIR"
    umask 077
    cat > "$SECRETS_FILE" <<EOF
---
# Сгенерировано scripts/deploy-remote.sh $(date -Is).
# Переиспользуется последующими деплоями. Не редактировать вручную: значения
# должны совпадать с теми, с которыми инициализированы PostgreSQL и MinIO.
tacitus_jwt_secret: "$(openssl rand -hex 32)"
tacitus_s3_access_key: "$(openssl rand -hex 16)"
tacitus_s3_secret_key: "$(openssl rand -hex 32)"
tacitus_postgres_password: "$(openssl rand -hex 24)"
mediamtx_publish_password: "$(openssl rand -hex 24)"
mediamtx_read_password: "$(openssl rand -hex 24)"
mediamtx_api_password: "$(openssl rand -hex 24)"
EOF
    chmod 600 "$SECRETS_FILE"
else
    info "Использую существующие секреты из $SECRETS_FILE"
fi

# --- запуск плейбука --------------------------------------------------------

ansible_args=(
    -e "@$SECRETS_FILE"
    # 127.0.0.1, а не реальный адрес: ansible.posix.synchronize копирует
    # frontend/ локальным rsync только когда видит localhost. С внешним адресом
    # он полез бы по SSH на самого себя.
    -e target_host=127.0.0.1
    -e connection_type=local
    -e "deploy_user=$deploy_user"
)

if [ "$enable_https" = true ]; then
    ansible_args+=(
        -e tacitus_enable_https=true
        -e "tacitus_domain=$domain"
        -e "ssl_certbot_staging=$certbot_staging"
    )
    [ -n "$email" ] && ansible_args+=(-e "tacitus_email=$email")
else
    # CORS-origins по умолчанию выводятся из target_host, а он подменён на
    # 127.0.0.1 — задаём адрес, по которому приложение открывают на самом деле.
    ansible_args+=(-e "tacitus_cors_origins=http://$public_host")
fi

[ -n "$tags" ] && ansible_args+=(--tags "$tags")

for var in ${extra_vars+"${extra_vars[@]}"}; do
    ansible_args+=(-e "$var")
done

info "Запуск ansible-playbook"
cd "$SRC_DIR/ansible"
exec ansible-playbook playbook.yml "${ansible_args[@]}"
