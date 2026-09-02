# Security

## 1. Transport Layer Security

### 1.1 Security Headers

Nginx добавляет следующие security headers ко всем ответам:

| Header                      | Значение                                                                                                            |
|-----------------------------|---------------------------------------------------------------------------------------------------------------------|
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains; preload`                                                                      |
| `Content-Security-Policy`   | `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'` |
| `X-Frame-Options`           | `DENY`                                                                                                              |
| `X-Content-Type-Options`    | `nosniff`                                                                                                           |
| `X-XSS-Protection`          | `1; mode=block`                                                                                                     |
| `Referrer-Policy`           | `strict-origin-when-cross-origin`                                                                                   |
| `Permissions-Policy`        | `geolocation=(), microphone=(), camera=()`                                                                          |

В production-шаблоне Ansible (`nginx-ssl.conf.j2`) добавлена директива `upgrade-insecure-requests` в CSP.

### 1.2 HTTPS Enforcement

- Production-конфигурация перенаправляет HTTP → HTTPS (`return 301 https://...`).
- Приложение **не использует cookies**: токены передаются в JSON-теле ответа и
  в заголовке `Authorization: Bearer`. Соответственно нет и флагов `Secure` /
  `HttpOnly` / `SameSite` — хранение токенов на стороне клиента остаётся
  ответственностью фронтенда (сейчас `localStorage`, см. `docs/frontend.md`).

---

## 2. Authentication & Authorization Hardening

### 2.1 Rate Limiting

Установлен `slowapi` + `limits`. Ключ rate-limit формируется как
`user:<sub>` для авторизованных запросов и `ip:<client_ip>` для анонимных.

| Endpoint                 | Лимит  | Ключ         |
|--------------------------|--------|--------------|
| `POST /auth/login`       | 5/мин  | IP           |
| `POST /auth/register`    | 3/час  | IP           |
| `POST /auth/refresh`     | 10/мин | IP           |
| `POST /files/upload`     | 10/мин | пользователь |
| `GET /files/`            | 60/мин | пользователь |
| `GET /files/stats`       | 10/мин | пользователь |
| `GET /files/{filename}`  | 30/мин | пользователь |
| `DELETE /files/{filename}` | 10/мин | пользователь |

Ключ везде вычисляет одна и та же функция `get_user_or_ip`. Для `/auth/*` она
на практике даёт IP просто потому, что в этих запросах обычно нет заголовка
`Authorization`. `POST /auth/logout` и `GET /auth/me` лимитов не имеют.

Клиентский IP берётся с учётом `TACITUS_TRUSTED_PROXY_HOPS` — см. §2.7, иначе
лимит обходится подделкой `X-Forwarded-For`.

При превышении возвращается `429 Too Many Requests`.

Хранилище счётчиков задаётся `TACITUS_RATE_LIMIT_STORAGE_URI`:

- `memory://` — в процессе (dev, не подходит для нескольких инстансов).
- `redis://redis:6379` — общее хранилище для production / multi-instance.

Библиотека `limits` не поддерживает PostgreSQL как storage, поэтому для распределённых инсталляций используется Redis.

### 2.2 Brute Force Protection

- Неудачные попытки входа хранятся в таблице `failed_login_attempts` (PostgreSQL)
  с окном 15 минут.
- Два независимых порога в этом окне:
  - **5** попыток на пару `(username, ip)` — блокирует эту пару, чтобы
    распределённый атакующий не мог заблокировать легитимного пользователя;
  - **20** попыток на `username` с любых IP — backstop против credential
    stuffing, размазанного по адресам.
- Каждая неудачная попытка пишется в `failed_login_attempts` и логируется
  событием `auth.login.failed` с `username` и `client_ip`.

> В таблицу `security_events` неудачные логины **не** попадают: туда пишет
> только `SuspiciousActivityMiddleware` (см. §6.2). `log_security_event`
> работает исключительно с логгером.

### 2.3 Password Policy

Пароль при регистрации должен соответствовать правилам:

- Минимум 8 символов
- Хотя бы одна заглавная буква
- Хотя бы одна строчная буква
- Хотя бы одна цифра
- Хотя бы один специальный символ

Валидация реализована через Pydantic `field_validator` в `RegisterRequest`.

**Username при регистрации:**

- Длина 3–64 символа
- Только `a-zA-Z0-9._-`

Верхняя граница совпадает с `VARCHAR(64)` в `users.username` и в таблицах
аудита. Без неё значение доходило до PostgreSQL, тот отвечал `DataError`, и
клиент получал `500` вместо `422`. `LoginRequest` ограничивает только длину —
чтобы неудачную попытку всегда можно было записать в `failed_login_attempts`,
но не отсекать учётные записи, созданные до введения правил.

### 2.4 Token Management

- Access token: TTL **15 минут**, содержит уникальный `jti` для отзыва.
- Refresh token: TTL **7 дней**, хранится в таблице `refresh_tokens` (PostgreSQL). Хранится только SHA-256 хеш токена, а
  не сам токен.
- `POST /auth/refresh` — обмен refresh token на **новую пару** токенов.
  Предъявленный refresh-токен одноразовый: он отзывается, взамен выдаётся
  новый (rotation).
- `POST /auth/logout` — отзывает оба токена: `jti` access-токена попадает в
  `revoked_tokens` через PostgreSQL upsert (`ON CONFLICT DO NOTHING`, для
  идемпотентности), refresh-токен помечается `revoked_at`.
- `decode_access_token()` проверяет `revoked_tokens` в БД перед валидацией.

**Reuse detection.** Отзыв refresh-токена выполняется одним
`UPDATE ... WHERE token_hash = ? AND revoked_at IS NULL`, поэтому из
нескольких параллельных запросов с одним токеном ровно один получает
`rowcount = 1`. Проигравшие (как и повторное предъявление уже потраченного
токена) трактуются как компрометация: отзывается **вся** семья refresh-токенов
пользователя, пишется событие `auth.refresh.reuse_detected`, клиент получает
`401`. Новая пара выдаётся только победителю — поэтому старый токен гасится
**до** выпуска нового.

### 2.5 S3/MinIO Download Authentication

Скачивание файлов из S3/MinIO происходит в два этапа:

1. **JWT-protected API:** клиент вызывает `GET /files/{filename}` с Bearer access token. Токен проверяется подписью и
   сверяется с чёрным списком
   `revoked_tokens` в PostgreSQL.
2. **Presigned URL:** после успешной аутентификации бэкенд генерирует краткосрочную подписанную ссылку через boto3
   (`generate_presigned_url`) и возвращает её клиенту. Клиент скачивает файл напрямую из S3/MinIO по этой ссылке.

```text
Client  -->  GET /files/{filename} (Bearer JWT)  -->  FastAPI
FastAPI -->  FileUrlResponse { url }              -->  Client
Client  -->  GET <presigned URL>                  -->  S3/MinIO
```

| Параметр              | Значение                                             | Описание                               |
|-----------------------|------------------------------------------------------|----------------------------------------|
| Endpoint              | `GET /files/{filename}`                              | Требует валидный access token          |
| Rate limit            | 30/минуту                                            | На пользователя (JWT `sub`)            |
| Presigned URL TTL     | `TACITUS_PRESIGNED_URL_EXPIRATION` (default 300 сек) | Время жизни прямой ссылки              |
| Auth на presigned URL | Подпись S3 credentials приложения                    | Клиент не предъявляет отдельных ключей |

Почему presigned URL считается безопасным:

- Ссылка **уникальна** и криптографически подписана — её невозможно подделать без секрета приложения.
- Срок действия **ограничен** (по умолчанию 5 минут), поэтому утечка ссылки раскрывает файл только на короткое время.
- Перед генерацией URL выполняется `head_object`, чтобы не выдавать ссылки на несуществующие файлы.

Production-рекомендации:

- Bucket policy должна **запрещать публичный доступ** ко всем объектам.
- MinIO Console (порт 9001) **не должен быть exposed наружу**.
- Значение `TACITUS_PRESIGNED_URL_EXPIRATION` должно быть минимально достаточным для скачивания файла клиентом.

### 2.6 Startup Secret Validation

При `TACITUS_ENVIRONMENT=production` приложение падает с `RuntimeError`, если
(`app/core/config.py::validate_startup_settings`):

- `jwt_secret` короче 32 символов;
- `s3_access_key` или `s3_secret_key` равны `minioadmin`;
- `cors_origins` содержит `*`.

> Проверки на конкретное значение секрета нет — только на длину. Дефолт
> `dev-jwt-secret-change-me` (24 символа) и compose-дефолт
> `change-me-in-production` (23) не проходят порог длины, но произвольный
> «слабый» секрет длиной ≥ 32 символов будет принят.

Дополнительный барьер на уровне деплоя: pre-check в `ansible/playbook.yml`
останавливает установку ещё до копирования файлов, если при
`tacitus_environment=production` секреты остались дефолтными.

### 2.7 Определение IP клиента (trusted proxy)

IP клиента управляет rate limiting, блокировкой brute-force и аудитом
`security_events`, поэтому он не должен выбираться самим клиентом.

`X-Forwarded-For` дополняется каждым прокси в цепочке, и **левые** элементы —
это то, что прислал клиент (в том числе подделанное). Доверять можно только тем
элементам, которые дописали наши собственные прокси, — они справа.

`TACITUS_TRUSTED_PROXY_HOPS` задаёт число обратных прокси перед приложением:

| Значение | Когда использовать | Поведение |
|----------|--------------------|-----------|
| `1` (default) | Стандартный стек: клиент → nginx → app | Берётся последний элемент `X-Forwarded-For` — адрес, который увидел nginx |
| `2`+ | Внешний балансировщик/CDN перед nginx | Берётся N-й элемент справа |
| `0` | Приложение доступно напрямую, без прокси | Заголовок игнорируется, используется адрес сокета |

Значение, не являющееся валидным IP, отбрасывается — чтобы подделанный
заголовок не попал в колонки аудита `VARCHAR(45)` и в ключи rate limiting.

> В `docker-compose.yml` порт `8000` сервиса `app` не публикуется наружу:
> приложение достижимо только через nginx, поэтому цепочка `X-Forwarded-For`
> всегда формируется нашим прокси. При изменении топологии обязательно
> пересмотрите `TACITUS_TRUSTED_PROXY_HOPS`.

Реализация — `app/core/ip.py::get_client_ip`, регрессионные тесты —
`tests/test_client_ip.py`.

---

## 3. Input Validation & File Upload Security

### 3.1 Filename Sanitization

Функция `sanitize_filename()` в `app/services/s3_service.py`:

- Отклоняет null bytes, path traversal (`../`, `..\`)
- Разрешает только `a-zA-Z0-9._-`
- Ограничивает длину 255 символами
- Запрещает скрытые файлы (начинающиеся с `.`)

Применяется во всех методах `S3Service`, принимающих `filename`. Некорректное имя возвращает `400 Bad Request`.

### 3.2 File Type Validation

Whitelist разрешённых MIME-типов:

```python
ALLOWED_TYPES = {
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
    "image/gif": {".gif"},
    "image/webp": {".webp"},
    "application/pdf": {".pdf"},
    "text/plain": {".txt"},
    "text/markdown": {".md"},
    "application/json": {".json"},
    "application/zip": {".zip"},
}
```

Неподдерживаемый тип возвращает `415 Unsupported Media Type`.

### 3.3 File Extension Validation

- Запрещены исполняемые расширения: `.exe`, `.bat`, `.sh`, `.py`, `.js`, `.php`
- Расширение файла должно соответствовать MIME-типу

---

## 4. CORS & API Security

### 4.1 CORS Hardening

- `allow_origins` берётся из переменной `TACITUS_CORS_ORIGINS` (default: `http://localhost:3000`).
- В production запрещён wildcard `*` в origins.

### 4.2 Request Limits

- FastAPI middleware `MaxRequestSizeMiddleware` ограничивает тело запроса 11 MB.
- Nginx: `client_max_body_size 11M`, `client_body_timeout 30s`, `client_header_timeout 10s`.

### 4.3 Error Handling

- В production generic 500 возвращает безопасное сообщение `"Internal server error"` без stack trace.
- В debug-режиме (`TACITUS_DEBUG=true`) возвращается текст исключения.

---

## 5. Secrets Management

### 5.1 Dev Defaults

`app/core/config.py` **сохраняет** dev-значения по умолчанию, чтобы стек
поднимался локально одной командой:

- `s3_access_key: str = Field(default="minioadmin", min_length=8)`
- `s3_secret_key: str = Field(default="minioadmin", min_length=8)`
- `jwt_secret: str = Field(default="dev-jwt-secret-change-me")`

Защита от их утечки в production — не отсутствие дефолтов, а два барьера:

1. `validate_startup_settings()` (§2.6) роняет приложение при
   `TACITUS_ENVIRONMENT=production` с дефолтными S3-ключами или коротким
   JWT-секретом.
2. Pre-check в `ansible/playbook.yml` не даёт запустить production-деплой с
   непереопределёнными секретами.

Пример полного набора переменных — в `.env.example`.

---

## 6. Audit Logging & Monitoring

### 6.1 Security Events Logging

Dedicated security logger (`app.core.security_logging`) фиксирует события:

| Событие                       | severity | поля                      |
|-------------------------------|----------|---------------------------|
| `auth.login.success`          | info     | ip, user                  |
| `auth.login.failed`           | warning  | ip, user                  |
| `auth.register`               | info     | ip, user                  |
| `auth.refresh.success`        | info     | ip, user                  |
| `auth.refresh.reuse_detected` | warning  | user                      |
| `auth.token.revoked`          | info     | ip, user, refresh_revoked |
| `file.upload`                 | info     | ip, user, file_name, size |
| `file.download`               | info     | ip, user, file_name       |
| `file.delete`                 | info     | ip, user, file_name       |
| `rate_limit.exceeded`         | warning  | ip, endpoint              |
| `db.integrity_error`          | error    | ip, endpoint              |
| `db.driver_error`             | error    | ip, endpoint              |
| `security.suspicious_activity`| warning  | ip, count, window_minutes |

> Все они идут **в лог** (см. `docs/backend.md` → «Security-лог»), а не в
> таблицу `security_events`. Единственный источник записей в таблице —
> `SuspiciousActivityMiddleware` с типом `security.response.failure`.

### 6.2 Suspicious Activity Detection

`SuspiciousActivityMiddleware` отслеживает 401/403 ответы по IP и сохраняет их в таблице `security_events` (PostgreSQL):

- Каждый 401/403 ответ порождает запись `security.response.failure`.
- При >100 запросов за 5 минут с одного IP логируется
  `security.suspicious_activity` (warning).
- Устаревшие события (старше 7 дней) очищаются периодически, чтобы таблица не росла неограниченно.

---

## 7. Container & Infrastructure Security

### 7.1 Non-root Containers

- **App:** `USER tacitus` (uid 1000) в `Dockerfile`
- **Nginx:** `user nginx;` в `nginx/nginx.conf` (worker processes от непривилегированного пользователя, master process
  от root для управления портами и privileges)

### 7.2 Read-only Root Filesystem

- `read_only: true` для сервисов `app` и `nginx` в `docker-compose.yml`
- `tmpfs: /tmp` для app
- Named volumes для nginx: `/var/cache/nginx`, `/var/log/nginx`, `/run`

### 7.3 Capability Dropping

```yaml
cap_drop:
  - ALL
cap_add:
  - NET_BIND_SERVICE  # bind to port 80/443
  - CHOWN              # chown nginx temp dirs
  - SETUID             # drop privileges to worker user
  - SETGID             # drop privileges to worker user
```

### 7.4 Resource Limits

```yaml
deploy:
  resources:
    limits:
      cpus: '1.0'
      memory: 512M
    reservations:
      cpus: '0.25'
      memory: 128M
```

---

## 8. Dependency & Supply Chain Security

### 8.1 Dependency Scanning

- Зависимости зафиксированы с SHA-256 хешами в `requirements.txt`
  (997 хешей, сгенерировано `pip-compile --generate-hashes`).

> ⚠️ Хуки `pip-audit` и `npm audit` **закомментированы** в
> `.pre-commit-config.yaml` (вместе с локальным `pytest`) — автоматического
> сканирования уязвимостей на коммите сейчас нет. Активные хуки: базовые
> проверки `pre-commit-hooks`, `ruff` и `ruff-format`. До включения хуков
> запускайте аудит вручную:

```bash
uv run pip-audit -r requirements.txt
cd frontend && npm audit --audit-level=moderate
```

Обновление lock-файла:

```bash
pip-compile --generate-hashes pyproject.toml -o requirements.txt
```

### 8.2 SBOM

Генерация SBOM:

```bash
python scripts/generate-sbom.py
```

Результат:

- `sbom-python.json` — Python dependencies
- `frontend/sbom.json` — frontend dependencies

---

## 9. Security Testing

### 9.1 Security-focused Unit Tests

| Тест                                 | Описание                                 |
|--------------------------------------|------------------------------------------|
| `test_password_policy_*`             | Валидация слабых/сильных паролей         |
| `test_sanitize_filename_*`           | Path traversal, null bytes, hidden files |
| `test_upload_forbidden_extension`    | `.exe` → 400/415                         |
| `test_upload_unsupported_media_type` | Неподдерживаемый MIME                    |
| `test_rate_limit_register`           | 429 после превышения лимита              |
| `test_brute_force_lockout`           | Блокировка после 5 попыток               |
| `test_token_blacklist`               | Revoked token → 401                      |
| `test_cors_preflight_rejected`       | Неизвестный origin → 400                 |

### 9.2 Security Headers Verification

`tests/test_nginx_security.py` проверяет наличие security headers в `nginx/nginx.conf` и production Ansible-шаблоне.

### 9.3 Penetration Testing Checklist

- [x] Stack trace не возвращается в production (`test_generic_exception_handler_production`)
- [x] `.env` недоступен через nginx (`location /.env { deny all; }`)
- [x] MinIO console (порт 9001) не экспонируется наружу в `docker-compose.yml`
- [x] Security headers проверены через unit-тесты nginx-конфигурации

---

## 10. Incident Response & Secret Rotation

### Ротация JWT Secret

1. Сгенерировать новый секрет: `openssl rand -base64 48`
2. Обновить `TACITUS_JWT_SECRET` в окружении
3. Rolling restart инстансов
4. Пользователи получают новые токены через `/auth/login` или `/auth/refresh`

### Ротация S3 Credentials

1. Создать новую пару ключей в MinIO/AWS IAM
2. Обновить `S3_ACCESS_KEY` и `S3_SECRET_KEY`
3. Rolling restart приложения
4. Удалить старые ключи после проверки

### Компрометация токена

1. Вызвать `POST /auth/logout` с токеном → blacklist
2. При массовой утечке — ротация `jwt_secret`
3. Информирование пользователей
4. Анализ логов

---

## 11. Deployment Security Checklist

- [ ] `TACITUS_ENVIRONMENT=production`
- [ ] `TACITUS_JWT_SECRET` — минимум 32 символа, не дефолт
- [ ] S3 credentials — не `minioadmin`
- [ ] HTTPS включён
- [ ] Security headers настроены в nginx
- [ ] CORS origins не содержат `*`
- [ ] Rate limiting активен (в production рекомендуется Redis backend)
- [ ] PostgreSQL настроен, миграции применены (`alembic upgrade head`)
- [ ] `TACITUS_DATABASE_URL` и `TACITUS_RATE_LIMIT_STORAGE_URI` заданы корректно
- [ ] Non-root контейнеры
- [ ] `read_only: true` для app и nginx
- [ ] `.env` недоступен через nginx

---
