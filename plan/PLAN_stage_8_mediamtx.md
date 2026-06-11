# План проекта Tacitus — мульти-потоковая архитектура и шифрование MediaMTX

## 1. Цель

Расширить интеграцию с **MediaMTX** для поддержки **множественных потоков** с
индивидуальной конфигурацией и обеспечить **шифрование** на транспортном и
прикладном уровнях.

**Конечный результат:**

- Пользователь может создавать, редактировать и удалять видео-потоки через
  FastAPI REST API и React UI.
- Каждый поток имеет индивидуальные параметры (запись, аутентификация,
  source-камера).
- Публикация и чтение потоков защищены per-path credentials (`publishUser`,
  `readUser`).
- Опционально доступен **RTSPS** (RTSP over TLS) для шифрования сигнального
  канала.
- Записи (fmp4-сегменты) могут шифроваться перед выгрузкой в S3/MinIO.

---

## 2. Архитектура

```
┌─────────────────┐      ┌──────────────────┐      ┌─────────────────┐
│   React SPA     │◄────►│  FastAPI (API)   │◄────►│   MediaMTX      │
│  Streams page   │      │  /streams/*      │      │  Control API    │
└─────────────────┘      └──────────────────┘      └────────┬────────┘
                                                            │
                       ┌────────────────────────────────────┘
                       │
              ┌────────▼────────┐
              │  RTSP / RTSPS   │◄── FFmpeg / IP-camera
              │   Port 8554     │
              └────────┬────────┘
                       │
              ┌────────▼────────┐
              │  ./recordings   │──► mediamtx-cleanup ──► S3/MinIO
              └─────────────────┘          (encrypt at rest)
```

### Компоненты

| Компонент         | Назначение                                           |
|-------------------|------------------------------------------------------|
| **FastAPI**       | CRUD потоков, управление credentials, прокси к MTX   |
| **MediaMTX**      | RTSP/RTSPS сервер, запись, per-path auth             |
| **React**         | UI списка потоков, форма добавления/редактирования   |
| **S3/MinIO**      | Хранение зашифрованных архивов записей               |
| **PostgreSQL**    | Хранение конфигураций потоков, пользователей, метаданных |
| **Ansible**       | Деплой TLS-сертификатов, ротация credentials         |

---

## 3. Этапы реализации

### 3.1. Управление множественными потоками

#### 3.1.1. Модель данных и хранение конфигурации

- [x] Создать Pydantic-схемы:
  - `StreamConfig` (name, source, record_enabled, publish_creds, read_creds,
    created_at, updated_at)
  - `StreamCreateRequest`, `StreamUpdateRequest`
- [ ] Настроить **PostgreSQL** в `docker-compose.yml`:
  - Сервис `postgres:18-alpine` с volume `postgres-data`
  - Переменные окружения: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`
  - Healthcheck через `pg_isready`
  - Сервис подключён к сети `tacitus-network`
- [ ] Добавить **SQLAlchemy** + **asyncpg** в зависимости:
  - `sqlalchemy[asyncio]>=2.0`, `asyncpg>=0.29`
  - Обновить `pyproject.toml` и `requirements.txt`
- [ ] Создать модели SQLAlchemy (`app/db/models.py`):
  - `Stream` (id, name, source, record_enabled, publish_user, publish_pass,
    read_user, read_pass, created_at, updated_at)
  - Уникальный индекс на `name`
- [ ] Настроить миграции **Alembic**:
  - `alembic init migrations`
  - Подключение к БД через `TACITUS_DATABASE_URL`
  - Автогенерация миграций для модели `Stream`
- [ ] Создать слой доступа к данным (`app/db/repositories/streams.py`):
  - `create_stream()`, `get_stream_by_name()`, `list_streams()`,
    `update_stream()`, `delete_stream()`
  - Async SQLAlchemy session через `async_sessionmaker`
- [ ] Рефакторинг `app/services/stream_config_service.py`:
  - Заменить in-memory dict на вызовы к репозиторию БД
  - Удалить `_streams: dict` и `_streams_lock`
- [ ] Добавить валидацию: уникальность имени потока (на уровне БД + схемы),
  допустимые символы в path

#### 3.1.2. Backend API — CRUD потоков

- [ ] `POST /streams` — создание нового потока
  - Валидация входных данных
  - Запись конфигурации в store
  - Отправка `POST /v3/config/paths/add/{name}` в MediaMTX Control API
    (динамическое добавление пути без перезагрузки)
- [ ] `GET /streams/config` — список всех сконфигурированных потоков (не только
  активных)
- [ ] `GET /streams/config/{path}` — детали конфигурации конкретного потока
- [ ] `PATCH /streams/config/{path}` — обновление параметров (record_enabled,
  credentials, source)
  - Отправка `PATCH /v3/config/paths/edit/{name}` в MediaMTX
- [ ] `DELETE /streams/config/{path}` — удаление потока
  - Отправка `DELETE /v3/config/paths/delete/{name}` в MediaMTX
  - Опционально: удаление связанных записей из `./recordings/{path}/`
- [ ] Все endpoints защищены JWT (`Depends(get_current_user)`)

#### 3.1.3. Индивидуальные настройки per-path

- [ ] Поддержка параметров в `mediamtx.yml` (и Jinja2-шаблоне):
  - `record: yes/no` — запись для конкретного потока
  - `source: publisher | rtsp://camera-url` — источник
  - `publishUser`, `publishPass` — аутентификация публикации
  - `readUser`, `readPass` — аутентификация чтения
  - `publishIPs`, `readIPs` — IP- whitelist
- [ ] Генерация credentials: случайные пароли через `secrets.token_urlsafe()`
  или хеширование `sha256` с `openssl`
- [ ] Обновление `pathDefaults` для дефолтных настроек (record, segmentDuration)

#### 3.1.4. Frontend — UI управления потоками

- [ ] Новая страница `/streams` (или модальное окно в существующем layout)
- [ ] Таблица потоков со столбцами:
  - Name, Source, Status (ready/not ready), Readers count, Record enabled
  - Кнопки: **Edit**, **Delete**, **View details**
- [ ] Форма создания/редактирования потока:
  - Поля: Name, Source type (publisher / camera URL), Record toggle,
    Publish/Read credentials (auto-generated или custom)
- [ ] Интеграция с API клиентом (`streamsApi.create()`, `update()`, `delete()`)

---

### 3.2. Шифрование потоков на MediaMTX

#### 3.2.1. RTSPS (RTSP over TLS)

- [ ] Исследовать поддержку RTSPS в `bluenviron/mediamtx:1.15.1-ffmpeg`
  - Параметры `encryption`, `rtspsAddress`, `serverKey`, `serverCert`
  - Режимы: `no` / `optional` / `strict`
- [ ] Сгенерировать self-signed сертификат для локальной разработки:
  ```bash
  openssl req -x509 -newkey rsa:4096 -keyout server.key -out server.crt \
    -days 365 -nodes -subj "/CN=localhost"
  ```
- [ ] Обновить `mediamtx.yml`:
  ```yaml
  rtsp: yes
  encryption: optional          # или strict
  rtspAddress: :8554            # plain RTSP
  rtspsAddress: :8322           # RTSP over TLS
  serverKey: /certs/server.key
  serverCert: /certs/server.crt
  ```
- [ ] Обновить `docker-compose.yml`:
  - Проброс RTSPS-порта `${TACITUS_MTX_RTSPS_PORT:-8322}`
  - Volume `./certs:/certs:ro`
- [ ] Обновить `ansible/roles/mediamtx`: копирование сертификатов на хост,
  настройка прав `600` на private key
- [ ] Обновить `mediamtx.yml.j2`: условная генерация RTSPS-блока
- [ ] Тестирование:
  ```bash
  ffmpeg -re -f lavfi -i testsrc -c:v libx264 -f rtsp \
    rtsps://localhost:8322/secure-live
  ```

#### 3.2.2. Аутентификация публикации и чтения (per-path)

- [ ] Настроить `publishUser` / `publishPass` в `pathDefaults` или per-path
- [ ] Настроить `readUser` / `readPass`
- [ ] Поддержка **sha256-хешированных** паролей (безопаснее plain text):
  ```bash
  echo -n "password" | openssl dgst -binary -sha256 | openssl base64
  # → sha256:BASE64_HASH
  ```
- [ ] Генерация credentials в FastAPI при создании потока
- [ ] Хранение credentials: **не в plain text в репозитории**
  - Локально: через `.env` или runtime-генерация
  - Production: Vault / Ansible Vault / AWS Secrets Manager (подготовка)
- [ ] Проверка отказа при неверных credentials:
  ```bash
  ffplay rtsp://wrong:user@localhost:8554/cam1   # → 401 Unauthorized
  ```

#### 3.2.3. Шифрование записей (encryption at rest)

- [ ] Исследовать встроенное шифрование записей MediaMTX (вероятно,
  отсутствует в v1.15.1)
- [ ] **Альтернатива A — шифрование на уровне файловой системы:**
  - LUKS для директории `./recordings` (требует privileged container)
  - eCryptfs / fscrypt (userspace encryption)
  - Сложность: Docker volume + non-root user
- [ ] **Альтернатива B — шифрование при выгрузке в S3:**
  - `scripts/mediamtx-archive-manager.py`: шифрование файла перед
    `put_object()` через `cryptography.fernet`
  - Хранение ключа шифрования в `TACITUS_ARCHIVE_ENCRYPTION_KEY`
  - S3 Server-Side Encryption (SSE-S3 / SSE-KMS) через boto3
    `ServerSideEncryption='AES256'`
- [ ] **Альтернатива C — шифрование целевой директории хоста:**
  - Encrypted bind-mount на уровне хоста (Linux: dm-crypt/LUKS)
  - Не требует изменений в приложении
- [ ] Выбрать и реализовать **один** подход для MVP
- [ ] Обновить `archive-manager`: опциональное шифрование перед upload

#### 3.2.4. Интеграция TLS с Nginx и Docker

- [ ] Использование **общих сертификатов** с Nginx (Let's Encrypt):
  - Volume `/etc/letsencrypt:/certs:ro` в `mediamtx`
  - Симлинк или копирование `fullchain.pem` → `server.crt`,
    `privkey.pem` → `server.key`
- [ ] Ansible: автоматическое обновление сертификатов через cron + reload
  MediaMTX (SIGHUP или `docker compose restart mediamtx`)
- [ ] Поддержка **mtls** (mutual TLS) для RTSPS — исследовать необходимость

---

### 3.3. Инфраструктура и деплой

#### 3.3.1. Docker Compose

- [ ] Добавить в `docker-compose.yml`:
  - RTSPS-порт: `${TACITUS_MTX_RTSPS_PORT:-8322}:${TACITUS_MTX_RTSPS_PORT:-8322}`
  - Volume для сертификатов: `./certs:/certs:ro` (или shared volume с nginx)
  - Переменная `MTX_ENCRYPTION`
  - Сервис `postgres:18-alpine` с volume `postgres-data`
  - Сервис `app` зависит от `postgres` (`depends_on` + `condition: service_healthy`)
  - Init-контейнер `db-migrations` для `alembic upgrade head`
- [ ] Обновить healthcheck: проверка и RTSP, и RTSPS портов (опционально)
- [ ] Обновить `mediamtx-cleanup`: поддержка `TACITUS_ARCHIVE_ENCRYPTION_KEY`

#### 3.3.2. Ansible

- [ ] Обновить `ansible/roles/mediamtx/tasks/main.yml`:
  - Создание директории `./certs` с правами `700`
  - Копирование сертификатов (self-signed или Let's Encrypt)
- [ ] Обновить `ansible/roles/mediamtx/templates/mediamtx.yml.j2`:
  - Условный блок RTSPS (`{% if mediamtx_encryption != 'no' %}`)
  - Цикл по `mediamtx_paths` с per-path credentials
- [ ] Обновить `ansible/group_vars/all.yml`:
  - `mediamtx_encryption: optional`
  - `mediamtx_rtsps_port: 8322`
  - `mediamtx_tls_cert_path: /opt/tacitus/certs/server.crt`
  - `mediamtx_tls_key_path: /opt/tacitus/certs/server.key`
  - `mediamtx_enable_auth: true`
- [ ] Pre-checks: проверка, что RTSPS-порт свободен
- [ ] Post-checks: проверка RTSPS handshake (`openssl s_client`)

#### 3.3.3. Переменные окружения

| Переменная                          | Default         | Описание                              |
|-------------------------------------|-----------------|---------------------------------------|
| `TACITUS_MTX_ENCRYPTION`            | `no`            | Режим RTSPS: `no` / `optional` / `strict` |
| `TACITUS_MTX_RTSPS_PORT`            | `8322`          | Порт RTSPS (RTSP over TLS)            |
| `TACITUS_MTX_TLS_CERT_PATH`         | `/certs/server.crt` | Путь к TLS-сертификату в контейнере |
| `TACITUS_MTX_TLS_KEY_PATH`          | `/certs/server.key` | Путь к TLS-ключу в контейнере       |
| `TACITUS_MTX_ENABLE_PER_PATH_AUTH`  | `true`          | Включить publish/read credentials     |
| `TACITUS_ARCHIVE_ENCRYPTION_KEY`    | —               | Ключ Fernet для шифрования архивов    |
| `TACITUS_DATABASE_URL`              | `postgresql+asyncpg://postgres:postgres@postgres:5432/tacitus` | URL подключения к PostgreSQL |

#### 3.3.4. PostgreSQL

- [ ] Добавить сервис `postgres` в `docker-compose.yml`:
  - Образ `postgres:18-alpine` (pinned)
  - Volume `postgres-data:/var/lib/postgresql/data`
  - Переменные: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`
  - Healthcheck: `pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}`
- [ ] Добавить init-контейнер `db-migrations`:
  - Образ на базе `tacitus-app`
  - Команда: `alembic upgrade head`
  - Зависимость от `postgres` (condition: service_healthy)
- [ ] Обновить `app/core/config.py`:
  - `database_url: str` с дефолтом `postgresql+asyncpg://postgres:postgres@postgres:5432/tacitus`
- [ ] Создать `app/db/engine.py`:
  - `create_async_engine()` с `TACITUS_DATABASE_URL`
  - `AsyncSessionLocal` через `async_sessionmaker`
- [ ] Обновить `ansible/roles/app_deploy`:
  - Добавить сервис `postgres` в шаблон `docker-compose.yml`
  - Настроить `POSTGRES_PASSWORD` через `.env.j2`
  - Backup-задача перед миграциями (опционально)

---

### 3.4. Тестирование

#### 3.4.1. Unit / integration tests

- [ ] Тесты CRUD API потоков (`test_streams_crud.py`):
  - Создание, обновление, удаление потока
  - Валидация дублирующего имени
  - Проверка JWT-защиты
- [ ] Тесты аутентификации потоков:
  - Публикация с верными credentials → успех
  - Публикация с неверными credentials → 401
  - Чтение с верными credentials → успех
- [ ] Тесты archive-manager с шифрованием:
  - Шифрование файла перед upload
  - Расшифровка и проверка целостности
  - No-op если ключ шифрования не задан

#### 3.4.2. End-to-end тестирование

- [ ] FFmpeg публикует поток с `publishUser:publishPass`:
  ```bash
  ffmpeg -re -f lavfi -i testsrc -c:v libx264 \
    -f rtsp rtsp://publisher:pass@localhost:8554/cam-auth
  ```
- [ ] FFmpeg публикует поток через **RTSPS**:
  ```bash
  ffmpeg -re -f lavfi -i testsrc -c:v libx264 \
    -f rtsp rtsps://localhost:8322/secure-live
  ```
- [ ] Проверка отказа при неверных RTSPS-сертификатах (strict mode)
- [ ] Проверка шифрования архивов: скачать из S3, расшифровать, сравнить с
  оригиналом

---

### 3.5. Документация

- [ ] Обновить `docs/backend.md`:
  - Раздел «Управление потоками» (CRUD API, схемы)
  - Раздел «Аутентификация потоков» (publish/read credentials)
- [ ] Обновить `docs/infrastructure.md`:
  - RTSPS-порт, TLS-сертификаты
  - Шифрование архивов (at rest)
- [ ] Обновить `docs/deployment.md`:
  - Переменные `mediamtx_encryption`, `mediamtx_rtsps_port`
  - Раздел «SSL-сертификаты для MediaMTX»
- [ ] Обновить `README.md`:
  - Endpoints `/streams/config/*`
  - Переменные `TACITUS_MTX_ENCRYPTION`, `TACITUS_ARCHIVE_ENCRYPTION_KEY`

---

## 4. Технологический стек

| Слой            | Технология              | Версия / Требование                    |
|-----------------|-------------------------|----------------------------------------|
| Media Server    | MediaMTX                | `bluenviron/mediamtx:1.15.1-ffmpeg`    |
| Streaming       | RTSP / RTSPS            | TCP + TLS (optional/strict)            |
| Auth            | HTTP Basic (per-path)   | `publishUser` / `readUser`             |
| Encryption      | TLS 1.2+, Fernet        | OpenSSL, `cryptography`                |
| Database        | PostgreSQL 16           | `asyncpg`, SQLAlchemy 2.0, Alembic     |
| Storage         | MinIO / S3              | SSE-S3 / SSE-KMS (опционально)         |
| Test            | pytest, ffmpeg, openssl | `openssl s_client` для RTSPS probe     |

---

## 5. Требования

### Функциональные (FR)

- **FR-001**: Пользователь может создать поток через `POST /streams/config` с
  указанием name, source, record_enabled.
- **FR-002**: Пользователь может обновить параметры потока
  (`PATCH /streams/config/{path}`) без перезапуска MediaMTX.
- **FR-003**: Пользователь может удалить поток; связанные записи удаляются
  опционально.
- **FR-004**: Каждый поток имеет уникальные `publishUser` / `publishPass`.
- **FR-005**: RTSPS доступен на настраиваемом порту (default `8322`).
- **FR-006**: Шифрование записей перед выгрузкой в S3 включено через env var.
- **FR-007**: Конфигурации потоков хранятся в PostgreSQL и переживают
  перезапуск контейнеров.

### Нефункциональные (NFR)

- **NFR-001**: Credentials не хранятся в plain text в репозитории.
- **NFR-002**: RTSPS-сертификаты ротируются без downtime (SIGHUP или
  graceful restart).
- **NFR-003**: Количество потоков не ограничено жёстко (лимит — ресурсы
  сервера).
- **NFR-004**: Повторный запуск Ansible не пересоздаёт сертификаты, если они
  валидны.
- **NFR-005**: PostgreSQL-данные сохраняются между перезапусками через Docker
  volume (`postgres-data`).

---

## 6. Риски и митигация

| Риск                                  | Митигация                                               |
|---------------------------------------|---------------------------------------------------------|
| MediaMTX v1.15.1 не поддерживает динамическое изменение `paths` без reload | Использовать Control API (`/v3/config/paths/add/...`) или fallback на reload |
| RTSPS требует TCP; UDP-RTP остаётся нешифрованным | Документировать: RTSPS шифрует только control plane; для media plane нужен SRTP (MediaMTX не поддерживает) |
| Self-signed certs вызывают warnings в FFmpeg/ffplay | Документировать `--no-check-certificate` для тестов; production — Let's Encrypt |
| Шифрование архивов увеличивает CPU load cleanup-сервиса | Тестировать производительность; Fernet — быстрый (AES-128-CBC) |
| Per-path credentials усложняют Jinja2-шаблон | Использовать Ansible `loop` + `dict` для `mediamtx_paths` |
| Добавление PostgreSQL усложняет локальный старт | `docker-compose.yml` поднимает Postgres автоматически; dev-defaults через `.env` |

---

## 7. Критерии завершения (Definition of Done)

- [ ] `POST /streams/config` создаёт поток; MediaMTX начинает принимать публикацию
  на новом path.
- [ ] `DELETE /streams/config/{path}` удаляет поток из MediaMTX.
- [ ] FFmpeg публикует поток с аутентификацией (`publishUser:publishPass`).
- [ ] FFmpeg публикует поток через RTSPS (`rtsps://host:8322/...`).
- [ ] Неверные credentials отклоняются MediaMTX (401).
- [ ] Архивный сегмент шифруется перед загрузкой в S3 и успешно
  расшифровывается.
- [ ] Ansible деплоит MediaMTX с RTSPS и per-path auth без ошибок.
- [ ] PostgreSQL поднимается в Docker Compose, миграции применяются
  автоматически (init-контейнер или `alembic upgrade head` в entrypoint).
- [ ] `ruff check .` и `pytest tests/` проходят без ошибок.
- [ ] Документация (`docs/`, `README.md`) обновлена.
