# Backend

## Технологии

- **Python** 3.14+
- **FastAPI** — веб-фреймворк
- **Pydantic** — валидация и сериализация
- **python-jose** — JWT токены
- **passlib** — хеширование паролей (pbkdf2_sha256)
- **boto3** — клиент для S3 (MinIO)

## Архитектура

```
app/
├── api/
│   ├── dependencies.py      # JWT dependency (get_current_user)
│   └── routers/
│       ├── auth.py          # /auth/* endpoints
│       ├── files.py         # /files/* endpoints
│       └── hello.py         # / endpoint
├── core/
│   ├── config.py            # Pydantic-settings (env vars)
│   ├── exceptions.py        # AppError и обработчики
│   ├── logging_config.py    # Structured logging
│   └── middleware.py        # RequestIdMiddleware
├── schemas/
│   ├── auth.py              # RegisterRequest, LoginRequest, TokenResponse
│   ├── files.py             # FileUploadResponse, FileListResponse, ...
│   └── hello.py             # HelloWorldResponse
└── services/
    ├── auth_service.py      # Регистрация, логин, JWT lifecycle
    ├── hello_service.py     # Hello World business logic
    └── s3_service.py        # CRUD операции с MinIO
```

## Аутентификация

- Регистрация и логин через JSON body (не query params — security)
- JWT access token с TTL 30 минут
- Пароли хешируются через `pbkdf2_sha256` (passlib)
- In-memory хранилище пользователей (MVP)

## Настройка

```bash
pip install -e ".[dev]"
```

Переменные окружения (префикс `TACITUS_`):

- `JWT_SECRET` — обязательно переопределить в production
- `JWT_ALGORITHM` — по умолчанию `HS256`
- `JWT_EXPIRATION_MINUTES` — по умолчанию `30`
- `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`

## Логирование

Логи настраиваются через переменные окружения с префиксом `TACITUS_LOG_`.

| Переменная         | По умолчанию               | Описание                                                             |
|--------------------|----------------------------|----------------------------------------------------------------------|
| `LOG_LEVEL`        | `INFO`                     | Уровень логирования: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `LOG_FORMAT`       | `json`                     | Формат: `json` (structured) или `text` (plain)                       |
| `LOG_DESTINATION`  | `stdout`                   | Куда писать: `stdout`, `file`, `both`                                |
| `LOG_FILE_PATH`    | `/var/log/tacitus/app.log` | Путь к файлу логов                                                   |
| `LOG_MAX_BYTES`    | `10485760`                 | Максимальный размер файла до ротации (10 МБ)                         |
| `LOG_BACKUP_COUNT` | `5`                        | Количество резервных копий                                           |

### Примеры конфигурации

**Development (только stdout, plain text):**

```bash
TACITUS_LOG_LEVEL=DEBUG
TACITUS_LOG_FORMAT=text
TACITUS_LOG_DESTINATION=stdout
```

**Production (файл + stdout, JSON):**

```bash
TACITUS_LOG_LEVEL=INFO
TACITUS_LOG_FORMAT=json
TACITUS_LOG_DESTINATION=both
TACITUS_LOG_FILE_PATH=/var/log/tacitus/app.log
```

**Только файл (для фоновых процессов):**

```bash
TACITUS_LOG_DESTINATION=file
TACITUS_LOG_FILE_PATH=/var/log/tacitus/app.log
```

### Security-лог

Security-события (auth, file uploads, suspicious activity) дополнительно пишутся в отдельный файл
`/var/log/tacitus/security.log` независимо от `LOG_DESTINATION`.

## Sentry

Опциональная интеграция с Sentry для отслеживания ошибок и performance monitoring.

| Переменная                  | По умолчанию  | Описание                                                   |
|-----------------------------|---------------|------------------------------------------------------------|
| `SENTRY_DSN`                | `''` (пустая) | DSN проекта Sentry. Если пустая — Sentry отключён          |
| `SENTRY_ENVIRONMENT`        | `development` | Окружение: `development`, `staging`, `production`          |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.1`         | Доля запросов, отправляемых в Sentry Performance (0.0–1.0) |

Включение:

```bash
TACITUS_SENTRY_DSN=https://xxx@yyy.ingest.sentry.io/zzz
TACITUS_SENTRY_ENVIRONMENT=production
TACITUS_SENTRY_TRACES_SAMPLE_RATE=0.1
```

Фильтрация PII: пароли, токены, JWT-секреты автоматически заменяются на `[FILTERED]` перед отправкой в Sentry.

### Docker

В `docker-compose.yml` для сервиса `app` создан volume `app-logs`, примонтированный в `/var/log/tacitus`. Логи
сохраняются между перезапусками контейнера.
