#!/usr/bin/env bash
#
# Полная очистка сервера от Tacitus — обратная операция к scripts/deploy.sh.
#
#   scripts/destroy.sh                 # очистить машину, на которой запущен
#   scripts/destroy.sh 1.2.3.4         # то же самое, но по SSH
#   scripts/destroy.sh 1.2.3.4 --yes   # без подтверждения (для CI)
#
# Что удаляется:
#   * стек проекта (docker compose down -v --rmi all) вместе с томами, то есть
#     с базой данных PostgreSQL, объектами MinIO, кэшем Redis и логами nginx;
#   * ВСЕ контейнеры, образы, тома, сети и build-кэш docker на хосте — не
#     только проектные (ограничить проектом: --project-only);
#   * каталог деплоя /opt/tacitus (там же лежит ./recordings), логи
#     /var/log/tacitus, секреты /etc/tacitus и временные каталоги
#     ~/.tacitus-deploy, которые оставляет deploy.sh;
#   * cron-задание обновления сертификата: оно дёргает уже удалённый контейнер
#     nginx и будет падать каждую ночь.
#
# Что НЕ трогается намеренно: сам движок docker, swap-файл, правила firewall,
# unattended-upgrades и сертификаты в /etc/letsencrypt. Это состояние хоста, а
# не проекта; выпуск сертификата к тому же ограничен лимитами Let's Encrypt,
# поэтому терять уже выданный ради полноты уборки не стоит.
#
# ДАННЫЕ УДАЛЯЮТСЯ БЕЗВОЗВРАТНО. Резервная копия базы — до запуска, не после.

set -euo pipefail

readonly DEPLOY_DIR="/opt/tacitus"
readonly SECRETS_DIR="/etc/tacitus"
readonly LOG_DIR="/var/log/tacitus"
# Каталог, в который scripts/deploy.sh распаковывает исходники на сервере.
readonly STAGING_DIR=".tacitus-deploy"

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

info() {
    printf '\033[1m==>\033[0m %s\n' "$*"
}

warn() {
    printf 'warning: %s\n' "$*" >&2
}

usage() {
    cat <<'EOF'
Использование: scripts/destroy.sh [host] [опции]

  [host]                IP или домен сервера. Без него скрипт работает на той
                        машине, где запущен.

Опции:
  --user USER           SSH-пользователь (по умолчанию root)
  --port PORT           SSH-порт (по умолчанию 22)
  --ssh-key PATH        Приватный ключ для подключения
  --project-only        Удалить только контейнеры, образы и тома Tacitus,
                        не трогая остальной docker на хосте
  --keep-secrets        Не удалять /etc/tacitus: следующий деплой переиспользует
                        те же пароли (иначе они генерируются заново)
  -y, --yes             Не спрашивать подтверждения
  -h, --help            Показать эту справку

Примеры:
  scripts/destroy.sh --project-only
  scripts/destroy.sh 1.2.3.4 --user ubuntu --ssh-key ~/.ssh/id_ed25519
EOF
}

host=""
ssh_user="root"
ssh_port="22"
ssh_key=""
project_only=false
keep_secrets=false
assume_yes=false

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help)      usage; exit 0 ;;
        --user)         ssh_user="${2:?--user требует значение}"; shift 2 ;;
        --port)         ssh_port="${2:?--port требует значение}"; shift 2 ;;
        --ssh-key)      ssh_key="${2:?--ssh-key требует значение}"; shift 2 ;;
        --project-only) project_only=true; shift ;;
        --keep-secrets) keep_secrets=true; shift ;;
        -y|--yes)       assume_yes=true; shift ;;
        -*)             die "неизвестная опция: $1 (см. --help)" ;;
        *)
            [ -z "$host" ] || die "хост указан дважды: '$host' и '$1'"
            host="$1"; shift ;;
    esac
done

# --- подтверждение ----------------------------------------------------------
#
# Спрашивается один раз и на управляющей машине: при запуске по SSH удалённая
# копия получает --yes и уже ничего не спрашивает.

confirm() {
    local target="${1:-этой машине}"
    local scope="весь docker (все контейнеры, образы и тома)"
    [ "$project_only" = true ] && scope="контейнеры, образы и тома Tacitus"

    [ "$assume_yes" = true ] && return 0

    printf 'На %s будут удалены: %s, база данных, %s и %s.\n' \
        "$target" "$scope" "$DEPLOY_DIR" "$LOG_DIR"
    printf 'Восстановить это будет нельзя. Введите yes для продолжения: '

    [ -t 0 ] || die "нет терминала для подтверждения; используйте --yes"

    local answer=""
    read -r answer
    [ "$answer" = "yes" ] || die "отменено"
}

# --- запуск на удалённом сервере --------------------------------------------

if [ -n "$host" ]; then
    command -v ssh >/dev/null || die "нужен ssh"

    self="${BASH_SOURCE[0]}"
    [ -f "$self" ] || die "скрипт запущен не из файла — на удалённый хост его не передать"

    confirm "$ssh_user@$host"

    ssh_opts=(-p "$ssh_port" -o StrictHostKeyChecking=accept-new)
    [ -n "$ssh_key" ] && ssh_opts+=(-i "$ssh_key")

    remote_args=(--yes)
    [ "$project_only" = true ] && remote_args+=(--project-only)
    [ "$keep_secrets" = true ] && remote_args+=(--keep-secrets)

    info "Очистка $ssh_user@$host"
    # Скрипт уезжает через stdin: копировать его во временный каталог, который
    # он сам же и удаляет, незачем.
    exec ssh "${ssh_opts[@]}" "$ssh_user@$host" \
        "bash -s -- ${remote_args[*]}" < "$self"
fi

# --- дальше всё выполняется на очищаемой машине -----------------------------

# Ansible кладёт копию скрипта в $DEPLOY_DIR/scripts. Bash дочитывает файл по
# ходу выполнения, поэтому удалить каталог из-под самого себя нельзя — работаем
# из копии во временном файле.
self="${BASH_SOURCE[0]}"
# Путь приводится к абсолютному: запуск вида `./scripts/destroy.sh` из каталога
# деплоя иначе не распознался бы как «скрипт внутри удаляемого каталога».
if [ -f "$self" ]; then
    self="$(cd "$(dirname "$self")" && pwd)/$(basename "$self")"
fi

if [ "${TACITUS_DESTROY_RELOCATED:-}" != 1 ] && [ -f "$self" ] \
    && [ "${self#"$DEPLOY_DIR"/}" != "$self" ]; then
    tmp_self="$(mktemp)"
    cp "$self" "$tmp_self"
    export TACITUS_DESTROY_RELOCATED=1
    exec bash "$tmp_self" "$@"
fi

if [ "$(id -u)" -ne 0 ]; then
    command -v sudo >/dev/null || die "нужен root или sudo"
    [ -f "$self" ] || die "нужен root: перезапустите под sudo"
    exec sudo -E bash "$self" "$@"
fi

confirm

# --- docker -----------------------------------------------------------------

if ! command -v docker >/dev/null 2>&1; then
    warn "docker не найден — пропускаю очистку контейнеров и образов"
elif ! docker info >/dev/null 2>&1; then
    warn "демон docker недоступен — пропускаю очистку контейнеров и образов"
else
    if [ -f "$DEPLOY_DIR/docker-compose.yml" ]; then
        info "Остановка стека и удаление томов (в том числе базы данных)"
        # Без .env compose ругается на пустые переменные, но состав сервисов и
        # томов от них не зависит — удалению это не мешает.
        if ! (cd "$DEPLOY_DIR" && docker compose down -v --rmi all --remove-orphans); then
            warn "docker compose down завершился с ошибкой — доудаляю по именам"
        fi
    fi

    if [ "$project_only" = true ]; then
        info "Удаление оставшихся объектов Tacitus"
        docker ps -aq --filter 'name=^/tacitus-' | xargs -r docker rm -f -v
        docker volume ls -q --filter 'name=tacitus' | xargs -r docker volume rm -f
        docker images -q 'tacitus-*' | xargs -r docker rmi -f
        docker network ls -q --filter 'name=tacitus' | xargs -r docker network rm >/dev/null 2>&1 || true
    else
        info "Удаление всех контейнеров"
        docker ps -aq | xargs -r docker rm -f -v

        info "Удаление всех образов, томов, сетей и build-кэша"
        # prune идёт первым: без контейнеров неиспользуемым становится всё,
        # так что одна команда снимает основную массу. Явные rmi и volume rm
        # после неё — страховка на случай объектов, которые prune пропустил.
        docker system prune -af --volumes >/dev/null
        docker images -aq | xargs -r docker rmi -f >/dev/null 2>&1 || true
        docker volume ls -q | xargs -r docker volume rm -f >/dev/null 2>&1 || true
    fi
fi

# --- файлы проекта ----------------------------------------------------------

info "Удаление файлов проекта"
rm -rf "$DEPLOY_DIR" "$LOG_DIR"

if [ "$keep_secrets" = true ]; then
    info "Секреты в $SECRETS_DIR сохранены"
else
    rm -rf "$SECRETS_DIR"
fi

# deploy.sh распаковывает исходники в домашний каталог того пользователя, под
# которым заходил по SSH, — он не обязательно root.
for home_dir in /root /home/*; do
    if [ -d "$home_dir/$STAGING_DIR" ]; then
        rm -rf "${home_dir:?}/$STAGING_DIR"
    fi
done

# --- cron -------------------------------------------------------------------

if command -v crontab >/dev/null 2>&1 && crontab -l >/dev/null 2>&1; then
    if crontab -l 2>/dev/null | grep -q 'tacitus-nginx'; then
        info "Удаление cron-задания обновления сертификата"
        # Ansible пишет задание двумя строками: комментарий-маркер и саму
        # команду, — убираем обе.
        crontab -l 2>/dev/null \
            | grep -v 'tacitus-nginx' \
            | grep -v '#Ansible: Certbot auto-renewal' \
            | crontab -
    fi
fi

info "Готово. Сервер очищен от Tacitus."
printf 'Нетронутыми оставлены: docker, swap, правила firewall, /etc/letsencrypt.\n'
