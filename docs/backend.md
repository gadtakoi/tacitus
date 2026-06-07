# Backend

## Технологии

- **Python** 3.14+
- **FastAPI** — веб-фреймворк
- **Pydantic** — валидация и сериализация
- **python-jose** — JWT токены
- **passlib** — хеширование паролей (pbkdf2_sha256)
- **boto3** — клиент для S3 (MinIO)
- **httpx** — HTTP-клиент для интеграции с MediaMTX

## Архитектура

```
app/
├── api/
│   ├── dependencies.py      # JWT dependency (get_current_user)
│   └── routers/
│       ├── auth.py          # /auth/* endpoints
│       ├── files.py         # /files/* endpoints
│       ├── hello.py         # / endpoint
│       └── streams.py       # /streams/* endpoints (MediaMTX monitoring)
├── core/
│   ├── config.py            # Pydantic-settings (env vars)
│   ├── exceptions.py        # AppError и обработчики
│   ├── logging_config.py    # Structured logging
│   └── middleware.py        # RequestIdMiddleware
├── schemas/
│   ├── auth.py              # RegisterRequest, LoginRequest, TokenResponse
│   ├── files.py             # FileUploadResponse, FileListResponse, ...
│   ├── hello.py             # HelloWorldResponse
│   └── streams.py           # StreamItem, StreamsListResponse
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

## MediaMTX интеграция

Приложение предоставляет прокси-endpoints к Control API MediaMTX:

| Endpoint          | Method | Auth   | Описание                                                |
|-------------------|--------|--------|---------------------------------------------------------|
| `/streams`        | GET    | Bearer | Список активных RTSP-потоков (`/v3/paths/list`)         |
| `/streams/{path}` | GET    | Bearer | Информация о конкретном потоке (`/v3/paths/get/{path}`) |

- Таймаут на запрос к MediaMTX: **10 секунд**
- Обрабатываются `NetworkError`, `TimeoutException` и HTTP-ошибки MediaMTX
- Логируются `request_id`, пользователь и имя потока без чувствительных данных

## Настройка

```bash
pip install -e ".[dev]"
```

### Переменные окружения (префикс `TACITUS_`)

| Переменная               | По умолчанию              | Описание                                                 |
|--------------------------|---------------------------|----------------------------------------------------------|
| `JWT_SECRET`             | `change-me-in-production` | Секрет для JWT (обязательно переопределить в production) |
| `JWT_ALGORITHM`          | `HS256`                   | Алгоритм JWT                                             |
| `JWT_EXPIRATION_MINUTES` | `30`                      | TTL access-токена                                        |
| `S3_ENDPOINT`            | `http://minio:9000`       | URL S3-хранилища                                         |
| `S3_ACCESS_KEY`          | `minioadmin`              | S3 access key                                            |
| `S3_SECRET_KEY`          | `minioadmin`              | S3 secret key                                            |
| `S3_BUCKET`              | `tacitus-files`           | Bucket для файлов                                        |
| `MTX_API_URL`            | `http://mediamtx:9997/v3` | URL Control API MediaMTX                                 |
| `MTX_RTSP_PORT`          | `8554`                    | Порт RTSP                                                |
| `MTX_MAX_AGE_DAYS`       | `7`                       | Макс. возраст записей (для cleanup)                      |
| `MTX_MAX_SIZE_GB`        | `50`                      | Макс. размер архива (для cleanup)                        |

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
