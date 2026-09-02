# Backend

## Технологии

- **Python** 3.14+
- **FastAPI** — веб-фреймворк
- **Pydantic** — валидация и сериализация
- **SQLAlchemy 2.0** + **asyncpg** — async ORM и драйвер PostgreSQL
- **Alembic** — миграции базы данных
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
├── db/
│   ├── engine.py            # Async SQLAlchemy engine и session factory
│   ├── models.py            # SQLAlchemy ORM модели
│   └── repositories/        # Слой доступа к данным
│       ├── auth.py          # User, RefreshToken, RevokedToken, ...
│       └── base.py          # Базовый класс репозитория
├── schemas/
│   ├── auth.py              # RegisterRequest, LoginRequest, TokenResponse
│   ├── files.py             # FileUploadResponse, FileListResponse, ...
│   ├── hello.py             # HelloWorldResponse
│   └── streams.py           # StreamItem, StreamsListResponse
└── services/
    ├── auth_service.py      # Регистрация, логин, JWT lifecycle
    ├── hello_service.py     # Hello World business logic
    ├── s3_service.py        # CRUD операции с MinIO
    └── stream_config_service.py  # Управление RTSP-потоками
```

## Аутентификация

- Регистрация и логин через JSON body (не query params — security)
- JWT access token с TTL 15 минут, содержит уникальный `jti` для отзыва
- Refresh token с TTL 7 дней
- Пароли хешируются через `pbkdf2_sha256` (passlib)
- Пользователи, refresh-токены, отозванные access-токены, неудачные попытки
  входа и security-события хранятся в **PostgreSQL** через SQLAlchemy 2.0
  (ранее использовалось in-memory хранилище)
- `get_current_user` получает `AsyncSession` и проверяет токен против таблицы
  `revoked_tokens` в БД

## Скачивание файлов

Endpoint `GET /files/{filename}` возвращает `FileUrlResponse`:

```json
{
  "filename": "report.pdf",
  "url": "https://minio.example.com/tacitus-files/report.pdf?X-Amz-Algorithm=..."
}
```

Flow:

1. Клиент отправляет запрос с заголовком `Authorization: Bearer <access_token>`.
2. `get_current_user` проверяет подпись JWT и сверяет `jti` с таблицей
   `revoked_tokens` в PostgreSQL.
3. Имя файла санитизируется (`sanitize_filename`).
4. `S3Service` проверяет существование объекта через `head_object` и генерирует
   presigned URL с TTL, заданным в `TACITUS_PRESIGNED_URL_EXPIRATION`.
5. Клиент выполняет `GET` по полученному URL напрямую к S3/MinIO для скачивания
   файла.

Ограничения:

- Endpoint защищён JWT и rate-limited (30 запросов в минуту на пользователя).
- Presigned URL не требует от клиента отдельных S3 credentials — он подписан
  ключами приложения.
- Если объект не найден в bucket, API возвращает `404 Not Found`.

## Rate limiting

- Глобальный лимитер на базе `slowapi` + `limits`.
- Ключ лимита: `user:<sub>` для авторизованных запросов, иначе `ip:<client_ip>`.
- Хранилище лимитов задаётся переменной `TACITUS_RATE_LIMIT_STORAGE_URI`:
  - `memory://` — в процессе (dev, не подходит для нескольких инстансов);
  - `redis://redis:6379` — общее хранилище для production / multi-instance.
- Библиотека `limits` не поддерживает PostgreSQL в качестве storage, поэтому для
  распределённых инсталляций используется Redis.

## Репозитории

Слой доступа к данным живёт в `app/db/repositories/` и изолирует SQLAlchemy
от бизнес-логики сервисов.

| Репозиторий | Назначение |
|-------------|------------|
| `UserRepository` | CRUD пользователей, поиск по `username`, проверка уникальности |
| `RefreshTokenRepository` | Создание, поиск по хешу, отзыв, очистка протухших refresh-токенов |
| `TokenBlacklistRepository` | Идемпотентное добавление `jti` в blacklist, проверка отозванных токенов |
| `FailedLoginAttemptRepository` | Запись неудачных входов, подсчёт за окно, очистка старых записей |
| `SecurityEventRepository` | Запись security-событий, подсчёт по IP, очистка устаревших событий |

Каждый репозиторий принимает `AsyncSession` в конструкторе и преобразует
`IntegrityError` в понятные исключения `AuthRepositoryError` / `RepositoryError`.
Это упрощает unit-тестирование с моками и позволяет в будущем заменить
SQLAlchemy на другой слой доступа к данным без изменения сервисов.

## Миграции

Миграции управляются Alembic. Файлы миграций находятся в
`migrations/versions/`.

```bash
# Применить все миграции
alembic upgrade head

# Откатить последнюю миграцию
alembic downgrade -1

# Сгенерировать новую миграцию после изменения моделей
alembic revision --autogenerate -m "describe your change"
```

При запуске через Docker Compose миграции применяет сервис `db-migrations`.

## Схема базы данных

Бэкенд использует PostgreSQL через SQLAlchemy 2.0 (asyncpg). Ниже перечислены
основные таблицы, их назначение, ключевые колонки и индексы.

### `users`

Учётные записи приложения.

| Колонка | Тип | Описание |
|---------|-----|----------|
| `id` | `UUID` | Первичный ключ, генерируется автоматически. |
| `username` | `VARCHAR` | Уникальное имя пользователя. |
| `password_hash` | `VARCHAR` | Хеш пароля (pbkdf2_sha256). |
| `is_active` | `BOOLEAN` | Флаг активности учётной записи. |
| `created_at` | `TIMESTAMP` | Дата и время создания записи. |
| `updated_at` | `TIMESTAMP` | Дата и время последнего обновления. |

**Индексы:**
- Уникальный индекс на `username`.

### `refresh_tokens`

Выпущенные refresh-токены. Привязаны к пользователю и имеют срок жизни.

| Колонка | Тип | Описание |
|---------|-----|----------|
| `id` | `UUID` | Первичный ключ. |
| `user_id` | `UUID` | Внешний ключ на `users.id` (`ON DELETE CASCADE`). |
| `token_hash` | `VARCHAR` | Хеш refresh-токена (хранить сам токен небезопасно). |
| `expires_at` | `TIMESTAMP` | Срок действия токена. |
| `revoked_at` | `TIMESTAMP` | Время отзыва (NULL, если токен активен). |
| `created_at` | `TIMESTAMP` | Время создания. |

**Индексы:**
- Индекс на `token_hash` для быстрого поиска при refresh/logout.
- Индекс на `user_id`.

### `revoked_tokens`

Чёрный список access-токенов (logout до истечения TTL).

| Колонка | Тип | Описание |
|---------|-----|----------|
| `id` | `UUID` | Первичный ключ. |
| `jti` | `VARCHAR` | JWT ID (`jti`) из payload отозванного токена. |
| `expires_at` | `TIMESTAMP` | Время истечения токена (для очистки старых записей). |
| `revoked_at` | `TIMESTAMP` | Время отзыва. |

**Индексы:**
- Уникальный индекс на `jti` (один токен отзывается один раз).
- Индекс на `expires_at` для периодической очистки.

### `failed_login_attempts`

Неудачные попытки входа. Используется для brute-force защиты.

| Колонка | Тип | Описание |
|---------|-----|----------|
| `id` | `UUID` | Первичный ключ. |
| `username` | `VARCHAR` | Имя пользователя, для которого была попытка. |
| `ip_address` | `VARCHAR` | IP-адрес клиента. |
| `created_at` | `TIMESTAMP` | Время попытки. |

**Индексы:**
- Составной индекс `(username, created_at)` для подсчёта попыток за окно.
- Индекс на `created_at` для очистки старых записей.

**Логика защиты:** если за последние 15 минут набралось 5 и более неудачных
попыток для одного `username`, вход временно блокируется.

### `security_events`

Security-события: неудачные логины, подозрительная активность и т.д.

| Колонка | Тип | Описание |
|---------|-----|----------|
| `id` | `UUID` | Первичный ключ. |
| `event_type` | `VARCHAR` | Тип события, например `auth.login.failed`. |
| `ip_address` | `VARCHAR` | IP-адрес клиента. |
| `username` | `VARCHAR` | Имя пользователя (опционально). |
| `details` | `JSONB` | Дополнительный контекст (status_code, count и т.д.). |
| `created_at` | `TIMESTAMP` | Время события. |

**Индексы:**
- Составной индекс `(ip_address, created_at)` для детекции подозрительной
  активности (например, > 100 событий 401/403 за 5 минут).
- Индекс на `event_type`.
- Индекс на `created_at` для ротации.

### `streams`

Конфигурация RTSP/RTMP потоков (уже существующая таблица).

| Колонка | Тип | Описание |
|---------|-----|----------|
| `id` | `UUID` | Первичный ключ. |
| `name` | `VARCHAR` | Уникальное имя потока. |
| `source` | `VARCHAR` | Источник потока. |
| `description` | `TEXT` | Описание. |
| `created_at` | `TIMESTAMP` | Время создания. |
| `updated_at` | `TIMESTAMP` | Время обновления. |

### Соглашения по проектированию

- Все первичные ключи — `UUID` (генерируются на стороне приложения или БД).
- Временные метки хранятся в UTC (`TIMESTAMP WITH TIME ZONE` предпочтительно,
  но для совместимости с SQLAlchemy по умолчанию используется `DateTime`).
- Для часто очищаемых таблиц (`failed_login_attempts`, `security_events`,
  `revoked_tokens`) предусмотрены индексы по `created_at` / `expires_at`.
- Внешние ключи используют `ON DELETE CASCADE` только там, где удаление
  родительской записи действительно должно удалять связанные данные
  (например, refresh-токены пользователя).

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

| Переменная                  | По умолчанию                       | Описание                                                 |
|-----------------------------|------------------------------------|----------------------------------------------------------|
| `JWT_SECRET`                | `change-me-in-production`          | Секрет для JWT (обязательно переопределить в production) |
| `JWT_ALGORITHM`             | `HS256`                            | Алгоритм JWT                                             |
| `JWT_EXPIRATION_MINUTES`    | `30`                               | TTL access-токена                                        |
| `DATABASE_URL`              | `postgresql+asyncpg://tacitus:...` | URL PostgreSQL через asyncpg                             |
| `RATE_LIMIT_STORAGE_URI`    | `memory://`                        | Backend rate limiting (`memory://` или `redis://...`)    |
| `TRUSTED_PROXY_HOPS`        | `1`                                | Сколько обратных прокси стоит перед приложением          |
| `S3_ENDPOINT`               | `http://minio:9000`                | URL S3-хранилища                                         |
| `S3_ACCESS_KEY`             | `minioadmin`                       | S3 access key                                            |
| `S3_SECRET_KEY`             | `minioadmin`                       | S3 secret key                                            |
| `S3_BUCKET`                 | `tacitus-files`                    | Bucket для файлов                                        |
| `PRESIGNED_URL_EXPIRATION`  | `300`                              | Время жизни presigned URL для скачивания (секунды)       |
| `MTX_API_URL`               | `http://mediamtx:9997/v3`          | URL Control API MediaMTX                                 |
| `MTX_RTSP_PORT`             | `8554`                             | Порт RTSP                                                |
| `MTX_MAX_AGE_DAYS`          | `7`                                | Макс. возраст записей (для cleanup)                      |
| `MTX_MAX_SIZE_GB`           | `50`                               | Макс. размер архива (для cleanup)                        |

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
