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

# Один раз создать тестовую базу
docker compose exec postgres psql -U tacitus -d tacitus \
  -c "CREATE DATABASE tacitus_test OWNER tacitus;"
```

Адрес по умолчанию — `postgresql+asyncpg://tacitus:tacitus@localhost:15432/tacitus_test`.
Порт **15432**, а не 5432: именно так compose пробрасывает postgres на хост
(`${POSTGRES_HOST_PORT:-15432}:5432`). Переопределяется через
`TACITUS_DATABASE_URL`.

> Без доступной PostgreSQL пропускается порядка 60 тестов — вся логика auth,
> репозиториев и миграций, — причём **молча**, обычным `skip`. Перед мержем
> убеждайтесь, что в итоговой строке pytest нет `skipped`, иначе изменения в
> `app/db/` и `app/services/auth_service.py` остаются непроверенными.

Тестовый engine и engine приложения принудительно используют `NullPool`:
каждый тест работает в собственном event loop (anyio открывает по одному на
тест), а пулированное соединение asyncpg остаётся привязанным к тому циклу,
в котором было создано. Без `NullPool` тест проходит поодиночке и падает в
общем прогоне.

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
| `test_startup_validation.py`         | Production-гарды `validate_startup_settings`       | нет      |
| `test_mediamtx_security.py`          | `authInternalUsers`, разделение прав, bind RTSP    | нет      |
| `test_streams_auth.py`               | Basic-аутентификация в MediaMTX Control API        | да       |
| `test_compose_pinning.py`            | Отсутствие установок пакетов при старте контейнеров | нет      |
| `test_dockerfile_pinning.py`         | Сборка образа по лок-файлу с хешами                | нет      |
| `test_transaction_boundaries.py`     | Атомарность ротации токена, durability аудита      | да       |
| `test_streams_router.py`             | Отказы MediaMTX, контракт ответа, rate limit       | да       |

## Покрытие

Текущее покрытие: **90%** (1383 statements, 140 missed) — 214 тестов, все
проходят.

Значение измеряется **с поднятой PostgreSQL** — без неё цифра занижена, потому
что пропущенные тесты не выполняют код `app/db` и `app/services/auth_service.py`.

```bash
docker compose up -d postgres
pytest tests/ --cov=app --cov-report=term-missing
```

Самые непокрытые места на сейчас:

| Модуль | Покрытие | Чего не хватает |
|--------|----------|-----------------|
| `db/engine.py` | 56% | Реальный `get_db_session` (в тестах подменяется) |
| `services/s3_service.py` | 84% | Ветки `ClientError` при list/upload/delete |
| `services/auth_service.py` | 67% | Ветки обработки ошибок БД (`AppError` на `DBAPIError`) |
| `core/exceptions.py` | 70% | Обработчики `integrity_error` / `dbapi_error` |

Бейдж в `README.md` обновляется вручную по результату этой команды.
