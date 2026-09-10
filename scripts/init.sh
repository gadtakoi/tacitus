#!/usr/bin/env bash
#
# Инициализация проекта и управление учётными записями: заводит пользователя,
# под которым можно войти в приложение, отключает и включает существующие.
#
#   scripts/init.sh                             # спросит логин и пароль
#   scripts/init.sh --username admin --generate # сгенерирует пароль
#   printf '%s' "$pass" | scripts/init.sh -u admin --password-stdin
#   scripts/init.sh --local --username admin    # без docker, python из окружения
#   scripts/init.sh --disable olduser           # отключить учётную запись
#   scripts/init.sh --enable olduser            # включить обратно
#
# Скрипт идемпотентен: повторный запуск с тем же логином ничего не меняет и
# завершается с кодом 0, поэтому его безопасно вызывать после каждого деплоя.
#
# Пароль не передаётся аргументом командной строки: argv виден в выводе ps и
# сохраняется в истории оболочки. Разбор аргументов и вся работа с БД — в
# scripts/init_project.py; здесь только выбор места запуска (контейнер app или
# локальный python) и проверки окружения.
#
# Коды выхода наследуются от python-скрипта:
#   0 — пользователь создан, уже существовал, либо изменено состояние записи
#   1 — база недоступна, миграции не применены, прочий сбой
#   2 — неверные аргументы, пароль не проходит политику, либо нет такого
#       пользователя (--disable/--enable)

set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Один и тот же относительный путь работает и в репозитории, и в образе:
# в контейнере WORKDIR=/app, куда Dockerfile кладёт scripts/init_project.py.
readonly PY_SCRIPT="scripts/init_project.py"
readonly SERVICE="app"

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

info() {
    printf '\033[1m==>\033[0m %s\n' "$*"
}

usage() {
    cat <<'EOF'
Использование: scripts/init.sh [опции]

  --local               Запустить python локально, а не в контейнере app.
                        Требует установленных зависимостей проекта и
                        TACITUS_DATABASE_URL, указывающего на доступный
                        PostgreSQL (для стека из docker compose это
                        postgresql+asyncpg://tacitus:tacitus@localhost:15432/tacitus).
  -h, --help            Показать эту справку

Остальные опции передаются в scripts/init_project.py:

  -u, --username NAME   Логин создаваемого пользователя
  --password-stdin      Прочитать пароль из первой строки stdin
  --generate            Сгенерировать пароль и напечатать его один раз
  --disable USERNAME    Отключить учётную запись и прекратить её сессии
  --enable USERNAME     Включить ранее отключённую учётную запись

Пароль также может быть передан через переменную TACITUS_INIT_PASSWORD.
Без указанных опций логин и пароль спрашиваются интерактивно.
EOF
}

run_local=false
py_args=()

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --local) run_local=true; shift ;;
        # Всё остальное — аргументы python-скрипта. Он же и сообщит об ошибке,
        # если опция неизвестна: дублировать разбор в двух местах значит рано
        # или поздно их рассинхронизировать.
        *) py_args+=("$1"); shift ;;
    esac
done

cd "$REPO_ROOT"

if [ "$run_local" = true ]; then
    [ -f "$PY_SCRIPT" ] || die "не найден $PY_SCRIPT"

    # Виртуальное окружение проекта, если оно есть: .venv/bin (Linux, macOS)
    # или .venv/Scripts (Windows). Иначе — python из PATH.
    python_bin=""
    for candidate in .venv/bin/python .venv/Scripts/python.exe; do
        if [ -x "$candidate" ]; then
            python_bin="$candidate"
            break
        fi
    done
    if [ -z "$python_bin" ]; then
        python_bin="$(command -v python3 || command -v python || true)"
    fi
    [ -n "$python_bin" ] || die "python не найден: активируйте окружение проекта"

    info "Создание пользователя (локальный python)"
    exec "$python_bin" "$PY_SCRIPT" "${py_args[@]}"
fi

command -v docker >/dev/null 2>&1 || die "docker не найден; для запуска без docker используйте --local"

# Контейнер должен быть запущен: exec выполняется в уже работающем процессе.
if [ -z "$(docker compose ps -q "$SERVICE" 2>/dev/null)" ]; then
    die "сервис '$SERVICE' не запущен. Поднимите стек: docker compose up -d"
fi

# Скрипт попадает в образ через Dockerfile. Если образ собран до его появления,
# понятное сообщение полезнее, чем ошибка python про отсутствующий файл.
if ! docker compose exec -T "$SERVICE" test -f "$PY_SCRIPT" 2>/dev/null; then
    die "в образе нет $PY_SCRIPT; пересоберите: docker compose up -d --build $SERVICE"
fi

# -T отключает выделение псевдотерминала. Он нужен, когда stdin — не терминал
# (конвейер с паролем, запуск из CI): иначе docker не сможет выделить TTY и
# упадёт. При интерактивном запуске TTY, наоборот, обязателен — без него
# getpass не сможет отключить эхо и пароль появится на экране.
exec_flags=()
if [ -t 0 ]; then
    # Git Bash на Windows не пробрасывает консоль Windows в docker напрямую;
    # winpty делает это за нас. Без него интерактивный запуск падает с
    # "the input device is not a TTY".
    case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*)
            if command -v winpty >/dev/null 2>&1; then
                info "Создание пользователя (контейнер $SERVICE)"
                exec winpty docker compose exec "$SERVICE" python "$PY_SCRIPT" "${py_args[@]}"
            fi
            ;;
    esac
else
    exec_flags+=(-T)
fi

info "Создание пользователя (контейнер $SERVICE)"
exec docker compose exec "${exec_flags[@]}" "$SERVICE" python "$PY_SCRIPT" "${py_args[@]}"
