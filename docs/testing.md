# Тестирование

## Инструменты

- **pytest** — unit и integration тесты
- **anyio** — плагин для async-тестов (маркер `@pytest.mark.anyio`)
- **pytest-cov** — покрытие кода
- **ruff** — линтинг и форматирование
- **mypy** — статическая типизация
- **pre-commit** — hooks перед коммитом

Все они объявлены в `[project.optional-dependencies].dev`:

```bash
pip install -e ".[dev]"   # или: uv sync --all-extras
```

## Запуск

```bash
# Все тесты
pytest tests/ -v

# Покрытие
pytest tests/ --cov=app --cov-report=term-missing

# Линтинг
ruff check .
ruff format .

# Типы
mypy app

# Pre-commit (все hooks)
pre-commit run --all-files
```

## Требования к окружению

Часть тестов работает с реальной PostgreSQL. `conftest.py` проверяет
доступность базы один раз за сессию и **пропускает** зависимые тесты, если она
недоступна — так остальной набор проходит без Docker.

```bash
# Поднять только базу
docker compose up -d postgres

# Адрес тестовой БД (по умолчанию именно такой)
export TACITUS_DATABASE_URL="postgresql+asyncpg://tacitus:tacitus@localhost:5432/tacitus_test"
```

> Без запущенной PostgreSQL пропускается порядка 60 тестов — вся логика auth,
> репозиториев и миграций. Перед мержем прогоняйте набор с базой, иначе
> изменения в `app/db/` и `app/services/auth_service.py` остаются непроверенными.

Схема создаётся через `Base.metadata.create_all` перед каждым тестом и
удаляется после; сессия привязана к транзакции, которая откатывается, поэтому
данные между тестами не протекают.

## Структура тестов

| Файл                                 | Что тестируется                                    | Нужна БД |
|--------------------------------------|----------------------------------------------------|----------|
| `test_hello.py`                      | Hello World endpoint                               | нет      |
| `test_health.py`                     | Health check                                       | нет      |
| `test_auth.py`                       | Регистрация, логин, refresh, logout, 401           | да       |
| `test_auth_schemas.py`               | Валидация username и пароля на уровне схем         | нет      |
| `test_repositories.py`               | Репозитории auth, атомарность отзыва токена        | да       |
| `test_migrations.py`                 | Применимость Alembic-миграций                      | да       |
| `test_files_router.py`               | Защищённые endpoints файлов (mock S3)              | да       |
| `test_s3_service.py`                 | `S3Service`, `sanitize_filename` (mock boto3)      | нет      |
| `test_stream_config.py`              | Схемы и сервис конфигурации потоков                | да       |
| `test_client_ip.py`                  | Определение IP клиента, подделка `X-Forwarded-For` | нет      |
| `test_rate_limit.py`                 | Лимиты и `429`                                     | да       |
| `test_cors.py`                       | CORS preflight, неизвестный origin                 | нет      |
| `test_middleware.py`                 | `MaxRequestSizeMiddleware`                         | нет      |
| `test_error_handling.py`             | Обработчики ошибок, отсутствие stack trace         | нет      |
| `test_logging.py`                    | `JsonFormatter`, request_id                        | нет      |
| `test_security_logging.py`           | `log_security_event`                               | нет      |
| `test_security_logging_integration.py` | Запись security-событий в БД                     | да       |
| `test_suspicious_activity.py`        | Детект всплесков 401/403                           | да       |
| `test_sentry.py`                     | Инициализация и фильтрация PII                     | нет      |
| `test_nginx_security.py`             | Security headers и наследование `add_header`       | нет      |
| `test_mediamtx_archive_manager.py`   | Ротация архивов MediaMTX (age + size)              | нет      |

## Покрытие

Значение покрытия измеряется **с поднятой PostgreSQL** — без неё цифра
занижена, потому что пропущенные тесты не выполняют код `app/db` и
`app/services/auth_service.py`.

```bash
docker compose up -d postgres
pytest tests/ --cov=app --cov-report=term-missing
```

Бейдж в `README.md` обновляется вручную по результату этой команды.
