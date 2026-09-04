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
│   ├── ip.py                # get_client_ip (trusted proxy)
│   ├── logging_config.py    # Structured logging
│   ├── middleware.py        # RequestIdMiddleware, MaxRequestSizeMiddleware
│   ├── rate_limit.py        # slowapi Limiter и key func
│   ├── security_logging.py  # Security events, SuspiciousActivityMiddleware
│   └── sentry.py            # Инициализация Sentry и фильтрация PII
├── db/
│   ├── engine.py            # Async SQLAlchemy engine и session factory
│   ├── models.py            # SQLAlchemy ORM модели
│   └── repositories/        # Слой доступа к данным
│       ├── auth.py          # User, RefreshToken, RevokedToken, ...
│       ├── base.py          # Базовый класс репозитория
│       └── streams.py       # StreamRepository (см. примечание ниже)
├── schemas/
│   ├── auth.py              # RegisterRequest, LoginRequest, TokenResponse
│   ├── files.py             # FileUploadResponse, FileListResponse, ...
│   ├── hello.py             # HelloWorldResponse
│   ├── stats.py             # FileStat, S3StatsResponse
│   └── streams.py           # StreamItem, StreamsListResponse, StreamConfig
└── services/
    ├── auth_service.py      # Регистрация, логин, JWT lifecycle
    ├── hello_service.py     # Hello World business logic
    ├── s3_service.py        # CRUD операции с MinIO
    └── stream_config_service.py  # Задел под CRUD потоков (не подключён)
```

> **Примечание.** `stream_config_service.py`, `db/repositories/streams.py` и
> схемы `StreamCreateRequest` / `StreamUpdateRequest` — подготовка к CRUD
> потоков из `plan/PLAN_stage_8_mediamtx.md`. Ни один роутер их пока не
> использует: `/streams/*` — это только прокси к Control API MediaMTX.
> Таблица `streams` создаётся миграцией, но приложением не читается.
>
> Поля credentials (`publish_user`, `publish_pass`, `read_user`, `read_pass`)
> из этого задела **удалены** миграцией `b2d8f6a3c714`. Они были
> спроектированы под параметры пути MediaMTX `publishUser`/`publishPass`,
> которых в закреплённой версии 1.15.1 больше нет: доступ выдаётся через
> глобальный `authInternalUsers` с `permissions[].path` (ADR-012). Хранить их
> означало держать пароли в открытом виде под API, который ничего не читает.

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
| `UserRepository` | Создание пользователя, поиск по `username`, проверка уникальности (update/delete не реализованы) |
| `RefreshTokenRepository` | Создание, поиск по хешу, атомарный отзыв, очистка протухших refresh-токенов |
| `TokenBlacklistRepository` | Идемпотентное добавление `jti` в blacklist, проверка отозванных токенов |
| `FailedLoginAttemptRepository` | Запись неудачных входов, подсчёт за окно, очистка старых записей |
| `SecurityEventRepository` | Запись security-событий, подсчёт по IP, очистка устаревших событий |

Каждый репозиторий принимает `AsyncSession` в конструкторе и преобразует
`IntegrityError` в понятные исключения `AuthRepositoryError` / `RepositoryError`.
Это упрощает unit-тестирование с моками и позволяет в будущем заменить
SQLAlchemy на другой слой доступа к данным без изменения сервисов.

### Границы транзакций

**Репозитории не коммитят.** Они стейджат работу на сессии — `flush` там, где
INSERT обязан дойти до БД (сработает констрейнт, заполнятся генерируемые
значения), — а транзакцию закрывает **сервис**. Откат при ошибке тоже на
сервисе.

Иначе каждый шаг многошаговой операции становился durable сам по себе:
ротация refresh-токена отзывала старый токен в одной транзакции и сохраняла
новый в другой, и сбой между ними оставлял пользователя без рабочей сессии и
без возможности откатиться. Теперь `store_refresh_token` закрывает обе записи
одним коммитом, а `logout_user` — отзыв access- и refresh-токена.

Коммит **намеренно** остаётся внутри отдельных сервисных функций там, где
запись обязана пережить неуспешный запрос:

| Место | Почему нужен собственный коммит |
|-------|--------------------------------|
| `record_failed_login` | Эндпоинт логина сразу отдаёт 401; откат вместе с запросом обнулил бы brute-force защиту |
| `_revoke_all_user_tokens` | Вызывается при детекте повторного использования токена и завершается 401; откат отменил бы реакцию на кражу |
| `SuspiciousActivityMiddleware` | Ведёт собственную сессию: аудит намеренно независим от транзакции запроса |

По этой же причине коммит **не** вынесен в зависимость `get_db_session`:
схема «commit на успехе, rollback на исключении» тихо сломала бы все три
сценария выше.

Регрессионные тесты — `tests/test_transaction_boundaries.py`.

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

**Логика защиты** (`auth_service.is_brute_force_locked`) — два порога в окне
15 минут:

- **5** неудачных попыток для пары `(username, ip_address)` — блокирует эту
  пару. Распределённый атакующий не может заблокировать легитимного
  пользователя, который заходит со своего IP.
- **20** неудачных попыток для `username` с **любых** IP — глобальный
  backstop против credential stuffing, размазанного по многим адресам.

Порог считается по `created_at > cutoff`, поэтому старые записи не влияют;
`cleanup_old` удаляет их после каждой неудачной попытки.

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

Конфигурация RTSP/RTMP потоков. Таблица создаётся миграцией
`64327e575f70`, но приложением пока не используется (см. примечание в разделе
«Архитектура»).

| Колонка | Тип | Описание |
|---------|-----|----------|
| `id` | `INTEGER` | Первичный ключ, `autoincrement` (не UUID — в отличие от остальных таблиц). |
| `name` | `VARCHAR(64)` | Имя потока, уникальный ключ `uq_stream_name`. |
| `source` | `VARCHAR(512)` | Источник: `publisher` либо URL `rtsp://` / `rtsps://` / `rtmp://`. |
| `record_enabled` | `BOOLEAN` | Включена ли запись/ротация архива. |
| `publish_user` | `VARCHAR(128)` | Логин для публикации потока (опционально). |
| `publish_pass` | `VARCHAR(128)` | Пароль для публикации (опционально). |
| `read_user` | `VARCHAR(128)` | Логин для чтения потока (опционально). |
| `read_pass` | `VARCHAR(128)` | Пароль для чтения (опционально). |
| `created_at` | `TIMESTAMPTZ` | Время создания. |
| `updated_at` | `TIMESTAMPTZ` | Время обновления. |

> ⚠️ Колонки `publish_pass` / `read_pass` рассчитаны на хранение открытым
> текстом — MediaMTX требует пароль в конфиге. Прежде чем подключать CRUD
> потоков, решите вопрос их шифрования либо выноса в secret manager.

### Соглашения по проектированию

- Первичные ключи — `UUID`, генерируются приложением (`default=uuid.uuid4`).
  Исключение — `streams.id`: она создана раньше и использует `INTEGER`
  с `autoincrement`.
- Все временные метки — `TIMESTAMP WITH TIME ZONE` (`DateTime(timezone=True)`),
  значения в UTC. Без `timezone=True` `create_all` выдал бы наивный
  `TIMESTAMP` и разошёлся бы с миграциями.
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
- Rate limit: **60/минуту** на пользователя для обоих endpoints
- Логируются `request_id`, пользователь и имя потока без чувствительных данных;
  тело ответа MediaMTX в лог не пишется

### Отображение отказов на статусы

Каждый способ отказа сопоставлен с явным кодом — непойманное исключение
выглядело бы как 500 «приложение сломалось», хотя проблема на стороне
MediaMTX или в конфигурации.

| Ситуация | Ответ клиенту |
|----------|---------------|
| `TimeoutException` (connect/read) | 504 |
| `NetworkError` (отказ соединения, обрыв) | 503 |
| `RemoteProtocolError`, `UnsupportedProtocol`, `TooManyRedirects` | 502 |
| `InvalidURL` — некорректный `TACITUS_MTX_API_URL` | 502 |
| MediaMTX ответил 404 | 404 «Stream not found» |
| MediaMTX ответил 401/403 | 502, отдельная запись в лог: расходятся `TACITUS_MTX_API_USER/PASSWORD` и `authInternalUsers` |
| Прочие 4xx/5xx от MediaMTX | 502 |
| Тело ответа не JSON | 502 |
| JSON не соответствует схеме | 502 |

> `InvalidURL` в httpx **не** наследуется от `HTTPError`, поэтому ловится
> отдельно — catch-all только по `HTTPError` его бы пропустил.

Ответ валидируется по схеме внутри `_fetch_from_mediamtx`, а не в endpoint:
иначе несоответствие контракта всплывало бы как 500 на сериализации
`response_model`.

### Именование полей

Ответ отдаётся в **snake_case**, как и весь остальной API:
`page_count`, `item_count`, `conf_name`, `ready_time`, `bytes_received`,
`bytes_sent`.

Алиасы в схемах (`pageCount`, `confName`, …) нужны только для **разбора**
входящего JSON от MediaMTX и наружу не выходят. FastAPI по умолчанию
сериализует по алиасам, поэтому на обоих маршрутах явно указан
`response_model_by_alias=False` — без него имена полей MediaMTX
републиковались бы как наш собственный контракт. Настройка закреплена
тестом `test_responses_carry_no_camelcase_aliases`.

## Настройка

```bash
pip install -e ".[dev]"
```

### Переменные окружения (префикс `TACITUS_`)

Ниже — поля `Settings` из `app/core/config.py`. Все читаются с префиксом
`TACITUS_`; неизвестные переменные с этим префиксом игнорируются.

| Переменная                     | По умолчанию                       | Описание                                                 |
|--------------------------------|------------------------------------|----------------------------------------------------------|
| `ENVIRONMENT`                  | `development`                      | `production` включает `validate_startup_settings()`      |
| `DEBUG`                        | `false`                            | Текст исключения в ответе 500, SQL-echo                  |
| `JWT_SECRET`                   | `dev-jwt-secret-change-me`         | Секрет для JWT (в production ≥ 32 символов)              |
| `JWT_ALGORITHM`                | `HS256`                            | Алгоритм JWT                                             |
| `JWT_EXPIRATION_MINUTES`       | `15`                               | TTL access-токена                                        |
| `JWT_REFRESH_EXPIRATION_DAYS`  | `7`                                | TTL refresh-токена                                       |
| `CORS_ORIGINS`                 | `http://localhost:3000`            | Список origins через запятую                             |
| `DATABASE_URL`                 | `postgresql+asyncpg://tacitus:...` | URL PostgreSQL через asyncpg                             |
| `RATE_LIMIT_STORAGE_URI`       | `memory://`                        | Backend rate limiting (`memory://` или `redis://...`)    |
| `TRUSTED_PROXY_HOPS`           | `1`                                | Сколько обратных прокси стоит перед приложением          |
| `S3_ENDPOINT`                  | `http://minio:9000`                | URL S3-хранилища                                         |
| `S3_ACCESS_KEY`                | `minioadmin`                       | S3 access key                                            |
| `S3_SECRET_KEY`                | `minioadmin`                       | S3 secret key                                            |
| `S3_BUCKET`                    | `tacitus-files`                    | Bucket для файлов                                        |
| `PRESIGNED_URL_EXPIRATION`     | `300`                              | Время жизни presigned URL для скачивания (секунды)       |
| `MTX_API_URL`                  | `http://mediamtx:9997/v3`          | URL Control API MediaMTX                                 |
| `LOG_*`                        | см. раздел «Логирование»           | Настройки логирования                                    |
| `SENTRY_*`                     | см. раздел «Sentry»                | Настройки Sentry                                         |

> `TACITUS_MTX_RTSP_PORT`, `TACITUS_MTX_MAX_AGE_DAYS`, `TACITUS_MTX_MAX_SIZE_GB`
> и прочие `MTX_*`-порты **не являются** настройками приложения — их читает
> `docker-compose.yml` для сервисов `mediamtx` и `mediamtx-cleanup`. Список —
> в README, раздел «Переменные окружения».

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

Security-события (auth, file uploads, suspicious activity) идут в отдельный
логгер `security` с `propagate = False`, поэтому в основной лог не попадают.

Куда именно они пишутся, **зависит от `LOG_DESTINATION`**:

| `LOG_DESTINATION` | Назначение security-событий |
|-------------------|------------------------------|
| `file` или `both` | `security.log` рядом с `LOG_FILE_PATH` (при недоступности каталога — fallback в stdout) |
| `stdout` (default)| stdout; **отдельный файл не создаётся** |

То есть при дефолтной конфигурации файла `/var/log/tacitus/security.log` нет —
чтобы он появился, задайте `TACITUS_LOG_DESTINATION=file` или `both`.

Отдельно от логов часть событий пишется в таблицу `security_events`, но это
делает **только** `SuspiciousActivityMiddleware` (ответы 401/403). Вызовы
`log_security_event` (`auth.login.failed`, `file.upload`, …) в БД не пишут.

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
