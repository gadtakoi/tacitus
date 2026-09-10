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
│       ├── recordings.py    # /recordings/* endpoints (архив записей в S3)
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
│   ├── recordings.py        # RecordingItem, RecordingListResponse, ...
│   ├── stats.py             # FileStat, S3StatsResponse
│   └── streams.py           # StreamItem, StreamsListResponse, StreamConfig
└── services/
    ├── auth_service.py      # Регистрация, логин, JWT lifecycle
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
  `revoked_tokens` в БД, а также сверяет `users.is_active`
- Регистрация выключается переменной `TACITUS_REGISTRATION_ENABLED`; проверка
  стоит в роутере, поэтому `scripts/init_project.py`, вызывающий
  `register_user` напрямую, продолжает заводить учётные записи
- `POST /auth/password` меняет пароль владельцу токена: требует действующий
  пароль, обновляет хеш и в той же транзакции отзывает семейство
  refresh-токенов и `jti` текущего access-токена
- Отключение и включение учётной записи — `scripts/init.sh --disable|--enable`;
  отключение отзывает refresh-токены пользователя

Подробности модели — `docs/security.md` §2.6 «Жизненный цикл учётной записи».

**Цена проверки активности.** `get_current_user` добавляет к каждому
аутентифицированному запросу один `SELECT users.is_active` — в той же сессии
БД, что уже открыта ради проверки `jti`, и одной колонкой, а не загрузкой
`User` (та тянет за собой refresh-токены через `lazy="selectin"`). Без этой
проверки отключение учётной записи вступало бы в силу не раньше истечения
access-токена, а с учётом ротации — никогда.

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

## Перечень файлов

`GET /files/` отдаёт **страницу** объектов, лежащих в корне bucket'а:

```json
{
  "files": ["report.pdf", "photo.jpg"],
  "next_cursor": "1PxJ8...="
}
```

Размер страницы задаётся `?limit=` (1–1000, по умолчанию 100), продолжение —
`?cursor=` со значением `next_cursor` предыдущего ответа. `null` означает
последнюю страницу.

Листинг запрашивается с разделителем `/`, и это же отделяет файлы от архива
записей: загрузка проходит через `sanitize_filename`, поэтому созданный
сервисом файл не может содержать `/`, а всё вложенное — включая архив под
`TACITUS_S3_ARCHIVE_PREFIX` — S3 сворачивает в common prefixes, и в ответ они не
попадают.

Разделитель выбран не ради лаконичности, а ради цены страницы. Отбрасывать
ключи архива в Python пришлось бы **после** их чтения: при десятках тысяч
сегментов клиент листал бы архив по сто ключей за запрос, получая пустые
страницы, только чтобы добраться до файлов, отсортированных после него. С
разделителем весь архив стоит одну запись страницы.

> Отсюда следствие, о котором стоит помнить в клиенте: страница может содержать
> меньше `limit` файлов и при этом иметь `next_cursor` — S3 засчитывает
> свёрнутый префикс в размер страницы. Признак «есть ещё» — курсор, а не
> количество элементов.

`GET /files/stats` считает файлы и архив раздельно: `total_*` описывают то, что
показывает перечень, а `archive_files` / `archive_size_bytes` /
`archive_size_human` — записи. Иначе сводка на странице «Файлы» описывала бы
объекты, которых на ней нет: одна загрузка соседствовала бы в счётчике с каждой
когда-либо заархивированной записью. Это единственное место, которое по-прежнему
обходит bucket целиком: итог — не страница, и подменить его курсором нечем.

## Записи

Архивные сегменты (`recording` → `archive` в терминах [CONTEXT.md](../CONTEXT.md))
складывает в объектное хранилище `scripts/mediamtx-archive-manager.py`. Ключ
повторяет структуру каталога записей:

```
mediamtx-archives/cam1/2026-09-10_12-00-00-000000.mp4
└── префикс ──┘└ поток ┘└──── начало сегмента ─────┘
```

Bucket у архива и у пользовательских файлов общий, поэтому **префикс —
единственная граница между ними**. Он задаётся переменной
`TACITUS_S3_ARCHIVE_PREFIX` (по умолчанию `mediamtx-archives/`) и должен
совпадать с `S3_PREFIX` сервиса `mediamtx-cleanup` в `docker-compose.yml`.
Значение нормализуется при старте: ведущий `/` и `..` запрещены, завершающий
`/` дописывается — без него ключ `mediamtx-archives-old/secret.mp4` прошёл бы
проверку `startswith`, предназначенную для `mediamtx-archives/`.

| Endpoint             | Метод  | Auth   | Лимит  | Описание                                    |
|----------------------|--------|--------|--------|---------------------------------------------|
| `/recordings`        | GET    | Bearer | 60/мин | Страница архива; фильтр `stream`, пагинация |
| `/recordings/{key}`  | GET    | Bearer | 30/мин | Presigned URL сегмента (400, 404)           |
| `/recordings/{key}`  | DELETE | Bearer | 10/мин | Удаление сегмента (400)                     |

### Пагинация

`GET /recordings` возвращает одну страницу и курсор:

```json
{
  "items": [
    {
      "key": "mediamtx-archives/cam1/2026-09-10_12-00-00-000000.mp4",
      "stream": "cam1",
      "size_bytes": 154238976,
      "last_modified": "2026-09-10T12:59:59Z"
    }
  ],
  "next_cursor": "1PxJ8...="
}
```

`next_cursor` — это continuation token S3: непрозрачная строка, которую клиент
возвращает как `?cursor=`. `null` означает последнюю страницу. Размер страницы
задаётся `?limit=` (1–1000, по умолчанию 100) — верхняя граница совпадает с
`MaxKeys` самого S3.

Сплошной обход, как в `GET /files/`, здесь неприменим: архив растёт на сегмент
в час на каждый поток, и время ответа стало бы функцией от того, как давно
сервис пишет записи.

Фильтр `?stream=cam1` не отсеивает результат в Python, а сужает префикс
запроса к S3 до `mediamtx-archives/cam1/`. Имя потока проверяется тем же
валидатором, что и в остальном API (`StreamName`), до подстановки в префикс.

### Ключ как параметр пути

`{key:path}` принимает слэши, поэтому проверка ключа отличается от проверки
имени файла и вынесена в `sanitize_object_key()`:

1. запрет null-байтов, нормализация `\` → `/`, ограничение длины 1024 байта
   (лимит S3);
2. посегментная проверка: пустые сегменты, `.` и `..`, скрытые файлы и символы
   вне `[a-zA-Z0-9._-]` отклоняются — `basename` здесь не спасает, потому что
   структура каталогов должна сохраниться;
3. и только потом — обязательный префикс архива.

Порядок важен: попытка обхода получает ответ «path traversal», а не «выход за
префикс», и в журнале видно, что именно пробовали сделать. Любой отказ — это
`400`, а не `500`.

> Percent-encoding снимается до маршрутизации, поэтому `%2e%2e%2f` доезжает до
> валидатора уже как `../`. Литеральные `../` до приложения обычно не доходят
> вовсе: их схлопывают клиенты и прокси.

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
| `UserRepository` | Создание пользователя, поиск по `username`, проверка уникальности, признак активности, смена пароля, включение/отключение (delete не реализован) |
| `RefreshTokenRepository` | Создание, поиск по хешу, атомарный отзыв, очистка протухших refresh-токенов |
| `TokenBlacklistRepository` | Идемпотентное добавление `jti` в blacklist, проверка отозванных токенов |
| `FailedLoginAttemptRepository` | Запись неудачных входов, подсчёт за окно, очистка старых записей |
| `SecurityEventRepository` | Запись security-событий, подсчёт по IP, очистка устаревших событий |

Каждый репозиторий принимает `AsyncSession` в конструкторе и преобразует
`IntegrityError` в понятные исключения `AuthRepositoryError` / `RepositoryError`.
Это упрощает unit-тестирование с моками и позволяет в будущем заменить
SQLAlchemy на другой слой доступа к данным без изменения сервисов.

### Пул соединений

Engine создаётся с `pool_pre_ping=True`: перед выдачей соединения из пула
SQLAlchemy проверяет, живо ли оно, и молча заменяет мёртвое.

Без этого после перезапуска PostgreSQL — обслуживание, обновление, failover —
пул продолжал раздавать соединения к уже несуществующему backend-процессу, и
клиент получал **500** вместо прозрачного переподключения. Воспроизводится как
`docker restart tacitus-postgres` плюс любой запрос к БД.

`pool_recycle` не задан намеренно: pre-ping покрывает и мёртвое соединение, и
разорванное по таймауту, а профилактическое пересоздание по возрасту решало бы
ту же задачу ценой лишних переподключений без наблюдаемой причины.

Регрессионные тесты — `tests/test_db_reconnect.py`, включая тест, который
подтверждает саму проблему: с выключенным pre-ping следующее обращение к пулу
падает.

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

Конфигурация потоков. Таблица создаётся миграцией `64327e575f70`; колонки
обрезки добавляет `c7f1a95d3e40`. Доступна через `/stream-configs` (см. ниже).

| Колонка | Тип | Описание |
|---------|-----|----------|
| `id` | `INTEGER` | Первичный ключ, `autoincrement` (не UUID — в отличие от остальных таблиц). |
| `name` | `VARCHAR(64)` | Имя потока, уникальный ключ `uq_stream_name`. |
| `source` | `VARCHAR(512)` | Источник: `publisher` либо URL `rtsp://` / `rtsps://` / `rtmp://`. |
| `record_enabled` | `BOOLEAN` | Включена ли запись/ротация архива. |
| `crop_enabled` | `BOOLEAN` | Обрезать ли кадр в записанных сегментах. По умолчанию `false`. |
| `crop_top_percent` | `INTEGER` | Доля высоты кадра, срезаемая сверху, в процентах. |
| `crop_bottom_percent` | `INTEGER` | Доля высоты кадра, срезаемая снизу, в процентах. |
| `created_at` | `TIMESTAMPTZ` | Время создания. |
| `updated_at` | `TIMESTAMPTZ` | Время обновления. |

Ограничения уровня БД: `ck_stream_crop_top_percent` и
`ck_stream_crop_bottom_percent` держат каждую долю в 0..90,
`ck_stream_crop_total_percent` — их сумму не выше 90 (минимум 10 % кадра
остаётся). Те же правила проверяются pydantic на границе HTTP; дублирование
намеренное — `video-processor` читает таблицу напрямую, а обрезка замещает
запись безвозвратно.

Колонки `publish_user` / `publish_pass` / `read_user` / `read_pass` существовали
в первой версии таблицы и удалены миграцией `b2d8f6a3c714`: MediaMTX 1.15.1 не
имеет per-path учётных данных, доступ раздаётся через `authInternalUsers`
(ADR-012, ADR-013).

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

## Конфигурации потоков

CRUD над таблицей `streams`. Отвечает за то, что Tacitus делает с уже
сделанными записями; **создание конфигурации не объявляет поток на MediaMTX** —
перечень принимаемых путей задаётся `mediamtx/mediamtx.yml` (в проде — `mediamtx_paths`
в Ansible).

| Endpoint | Method | Auth | Описание |
|----------|--------|------|----------|
| `/stream-configs` | GET | Bearer | Перечень конфигураций |
| `/stream-configs` | POST | Bearer | Создание (201, 409 при дубле имени) |
| `/stream-configs/{name}` | GET | Bearer | Одна конфигурация (404) |
| `/stream-configs/{name}` | PATCH | Bearer | Частичное изменение (404, 422) |
| `/stream-configs/{name}` | DELETE | Bearer | Удаление (204, 404) |

Лимит 60 запросов в минуту на пользователя или IP, как у `/streams`.

Префикс отдельный, а не `/streams/...`, не по вкусовым соображениям: в
`app/api/routers/streams.py` объявлен маршрут `GET /streams/{path:path}`,
который перехватил бы любой вложенный путь. Заодно это разводит прокси
мониторинга MediaMTX и конфигурацию в PostgreSQL.

Совокупный предел обрезки проверяется в сервисном слое после слияния PATCH с
сохранённой строкой: доля, допустимая сама по себе, может быть недопустима
поверх сохранённой (60 сверху + 40 снизу). Проверка до записи, а не опорой на
CHECK-ограничение — иначе клиент получил бы 500 вместо 422.

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

## Версия приложения

Версия объявлена в `app/__init__.py` — это то, что процесс сообщает о себе:
release для Sentry, `info.version` в OpenAPI и ответ `GET /settings/version`,
по которому её показывает шапка веб-интерфейса. Эндпоинт закрыт JWT: точный
номер сборки — подсказка для подбора известных уязвимостей, и анонимному
клиенту он не нужен. Версией процесса открывается и журнал: строка
`Starting tacitus <версия> in <окружение> environment` при старте.

Переменной окружения версия не является: образы собираются на сервере
развёртывания, поэтому версия характеризует сборку, а не установку.

Рядом с исходником живут семь копий числа — метаданные пакета, `package.json`
и `package-lock.json` фронтенда, теги образов в `docker-compose.yml`,
`tacitus_version` в `ansible/group_vars/all.yml` и примеры тегов в
`docs/audit.md` и `docs/infrastructure.md`. Обновляются все разом:

```bash
python scripts/bump_version.py 0.2.0
```

Скрипт ничего не коммитит и не расставляет теги git — только правит файлы.
Перечень мест лежит в `DECLARATIONS` в самом скрипте; оттуда же его читает
`tests/test_version_consistency.py`, поэтому новое место достаточно добавить
один раз, и оно сразу и обновляется, и проверяется.

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
| `S3_ARCHIVE_PREFIX`            | `mediamtx-archives/`               | Префикс ключей архива записей — граница между архивом и файлами |
| `REGISTRATION_ENABLED`         | `true`                             | Приём анонимных регистраций (`POST /auth/register`)      |
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

### Correlation id (`X-Request-Id`)

Заголовок `X-Request-Id` от клиента принимается, чтобы запрос можно было
связать со внешними системами, но значение **валидируется**: не длиннее 64
символов и только `A-Za-z0-9._-`. Некорректное значение отбрасывается, вместо
него генерируется UUID4.

Проверка нужна потому, что значение попадает сразу в три места: каждую строку
лога, заголовок ответа `x-request-id` и тело ответа при ошибке. Без неё клиент
мог раздувать логи произвольной длиной, а при `TACITUS_LOG_FORMAT=text` —
дописывать в лог поддельные строки переводом строки.

> Сопоставление выполняется через `fullmatch`, а не `match` с якорем `$`:
> `$` в Python совпадает и **перед завершающим переводом строки**, поэтому
> значение `"abc\n"` прошло бы проверку. Этот случай закреплён тестом.

Ограничение в 64 символа покрывает реально встречающиеся форматы: UUID (36),
`$request_id` nginx (32 hex), W3C `traceparent` (55).

Реализация — `app/core/logging_config.py::get_request_id`, тесты —
`tests/test_request_id.py`.

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
