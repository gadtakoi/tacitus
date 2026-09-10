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
| `POST /auth/logout`      | 10/мин | пользователь |
| `GET /auth/me`           | 60/мин | пользователь |
| `POST /files/upload`     | 10/мин | пользователь |
| `GET /files/`            | 60/мин | пользователь |
| `GET /files/stats`       | 10/мин | пользователь |
| `GET /files/{filename}`  | 30/мин | пользователь |
| `DELETE /files/{filename}` | 10/мин | пользователь |
| `GET /recordings`        | 60/мин | пользователь |
| `GET /recordings/{key}`  | 30/мин | пользователь |
| `DELETE /recordings/{key}` | 10/мин | пользователь |
| `GET /streams`           | 60/мин | пользователь |
| `GET /streams/{path}`    | 60/мин | пользователь |
| `GET /settings`          | 30/мин | пользователь |
| `GET /settings/version`  | 60/мин | пользователь |

Ключ везде вычисляет одна и та же функция `get_user_or_ip`. Для
`register`/`login`/`refresh` она на практике даёт IP просто потому, что в этих
запросах обычно нет заголовка `Authorization`; для `logout` и `me` — имя
пользователя из JWT.

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

#### Защита от перебора имён пользователей

`authenticate_user` выполняет **ровно одну** проверку пароля на любом исходе:
несуществующий аккаунт, отключённый аккаунт и неверный пароль стоят одинаково.
Для несуществующего и отключённого проверка идёт против одноразового хеша
`_DUMMY_PASSWORD_HASH`, созданного тем же `CryptContext`, что и настоящие
пароли, — то есть с той же схемой и стоимостью.

Без этого ранний `return` пропускал pbkdf2, ответ приходил заметно быстрее, и
атакующий мог перебирать имена пользователей, ни разу не угадав пароль.

> Это **не** constant-time аутентификация: разница в стоимости самого запроса
> к БД остаётся, но она на порядки меньше одного раунда pbkdf2. Убран сигнал,
> который имел практическое значение.

Ответ API одинаков во всех случаях — `401 Incorrect username or password`.
Регрессионные тесты — `tests/test_login_timing.py`.

### 2.3 Password Policy

Пароль при регистрации должен соответствовать правилам:

- Минимум 8 символов
- **Максимум 128 символов**
- Хотя бы одна заглавная буква
- Хотя бы одна строчная буква
- Хотя бы одна цифра
- Хотя бы один специальный символ

Валидация реализована через Pydantic `field_validator` в `RegisterRequest`.

**Верхняя граница пароля.** `LoginRequest` допускает до **4096** символов —
это `MAX_PASSWORD_SIZE` самой passlib. Граница выбрана так, чтобы, с одной
стороны, не отсекать учётные записи, созданные до её появления (пароль длиннее
4096 просто не мог быть захеширован), а с другой — не пропускать значение,
на котором passlib бросает `PasswordSizeError`. Раньше такое значение доходило
до `verify_password`, исключение никто не обрабатывал, и `POST /auth/login`
отвечал **500** вместо `422`.

> Стоимость pbkdf2 **не** зависит от длины пароля: замер даёт 11.4 мс на 8
> символах, 12.2 мс на 128 и 10.9 мс на 1024 — HMAC хеширует длинный ключ один
> раз, дальше цена итераций постоянна. То есть это ограничение входных данных
> и корректности обработки ошибок, а не защита от CPU-DoS.

**Username при регистрации:**

- Длина 3–64 символа
- Только `a-zA-Z0-9._-`

Верхняя граница совпадает с `VARCHAR(64)` в `users.username` и в таблицах
аудита. Без неё значение доходило до PostgreSQL, тот отвечал `DataError`, и
клиент получал `500` вместо `422`. `LoginRequest` ограничивает только длину —
чтобы неудачную попытку всегда можно было записать в `failed_login_attempts`,
но не отсекать учётные записи, созданные до введения правил.

**Создание пользователя вне HTTP.** Скрипт инициализации
`scripts/init_project.py` (см. [Деплой](deployment.md#инициализация-проекта-первый-пользователь))
проверяет учётные данные той же схемой `RegisterRequest` и создаёт запись тем
же сервисом `register_user`, поэтому обойти политику паролей «через CLI»
нельзя, а хеш совпадает с тем, что ожидает `POST /auth/login`. Пароль не
принимается аргументом командной строки: `argv` виден в `ps` и попадает в
историю оболочки. Сгенерированный пароль печатается один раз; введённый
оператором не выводится никогда — в том числе в сообщениях об ошибках
валидации, откуда `input_value` вырезан осознанно.

### 2.4 Token Management

- Access token: TTL **15 минут**, содержит уникальный `jti` для отзыва.
- Refresh token: TTL **7 дней**, хранится в таблице `refresh_tokens` (PostgreSQL). Хранится только SHA-256 хеш токена, а
  не сам токен.
- `POST /auth/refresh` — обмен refresh token на **новую пару** токенов.
  Предъявленный refresh-токен одноразовый: он отзывается, взамен выдаётся
  новый (rotation).
- `POST /auth/logout` — отзывает оба токена: `jti` access-токена попадает в
  `revoked_tokens` через PostgreSQL upsert (`ON CONFLICT DO NOTHING`, для
  идемпотентности), refresh-токен помечается `revoked_at`. Оба отзыва
  выполняются одной транзакцией в `logout_user`.
- **Проверка владельца при logout.** Refresh-токен отзывается, только если он
  принадлежит владельцу предъявленного access-токена. Раньше отзыв шёл по
  хешу токена, поэтому сессия, предъявившая чужой refresh-токен, выкидывала
  того пользователя из системы. Несовпадение пишется событием
  `auth.logout.foreign_refresh_token`.

  > Если access-токен раскодировать не удалось, владелец неизвестен и токен
  > отзывается по факту предъявления. Отказ здесь сломал бы выход для клиента
  > с истёкшим access-токеном, а владение refresh-токеном само по себе
  > является доказательством. То есть проверка — защита от перекрёстного
  > отзыва легитимной сессией и сигнал для расследования, а не жёсткая
  > граница.
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

#### Архив записей: та же модель, другая граница

`GET /recordings/{key}` работает по этой же схеме — JWT на endpoint, presigned
URL на объект, тот же TTL, — но принимает **ключ со слэшами**
(`mediamtx-archives/cam1/2026-09-10_12-00-00-000000.mp4`), а не плоское имя
файла. Санитизация имени здесь не подходит: `sanitize_filename` сводит вход к
basename и вместе со структурой каталога уничтожает адрес объекта.

Архив и пользовательские файлы лежат в **одном bucket**, поэтому единственное,
что отделяет одно от другого, — префикс `TACITUS_S3_ARCHIVE_PREFIX`
(по умолчанию `mediamtx-archives/`). Его проверяет `sanitize_object_key()`:

| Проверка                                    | Отклоняет                                              |
|---------------------------------------------|--------------------------------------------------------|
| null-байты, длина ≤ 1024 байт               | `seg\x00.mp4`, ключи сверх лимита S3                   |
| посегментный разбор после `\` → `/`         | `..`, `.`, пустые сегменты, `\`-варианты обхода        |
| скрытые сегменты                            | `mediamtx-archives/.hidden/segment.mp4`                |
| алфавит `[a-zA-Z0-9._-]` в каждом сегменте  | пробелы, `:`, `%`, всё нештатное                       |
| обязательный префикс архива                 | `secret.png`, `mediamtx-archives-old/secret.png`       |

Любой отказ — `400`, и ни один такой запрос не доходит до S3. Проверка префикса
идёт **последней**: попытка обхода в журнале выглядит как обход, а не как
промах мимо префикса.

Завершающий `/` в префиксе обязателен и дописывается при старте
(`Settings._normalize_archive_prefix`) — без него `startswith` пропустил бы
соседний префикс `mediamtx-archives-old/`.

> Percent-encoding снимается до маршрутизации, поэтому `%2e%2e%2f` приходит в
> валидатор уже как `../`; литеральные `../` до приложения обычно не доходят —
> их схлопывают клиенты и прокси. Тесты бьют по обеим формам
> (`tests/test_recordings_router.py`).

Удаление (`DELETE /recordings/{key}`) проходит через ту же проверку. Оно
необратимо: переход «запись → архив» уже удалил локальную копию, и объект в
S3/MinIO — единственный оставшийся экземпляр.

### 2.6 Startup Secret Validation

При `TACITUS_ENVIRONMENT=production` приложение падает с `RuntimeError`, если
(`app/core/config.py::validate_startup_settings`):

- `debug` включён (`TACITUS_DEBUG=true`);
- `jwt_secret` короче 32 символов;
- `s3_access_key` или `s3_secret_key` равны `minioadmin`;
- `cors_origins` содержит `*`.

> Проверка `debug` закрывает сразу два канала утечки: `generic_exception_handler`
> в debug-режиме возвращает клиенту текст исключения (§4.3), а
> `create_async_engine(echo=settings.debug)` пишет в логи каждый SQL-запрос
> вместе с bind-параметрами.

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

### 2.8 MediaMTX Authentication (RTSP и Control API)

MediaMTX поставляется с **анонимным дефолтом**: без секции
`authInternalUsers` встроенный список выдаёт `user: any` с пустым паролем
права `publish`, `read` и `playback` на все пути. Явное объявление секции
полностью вытесняет этот список.

Учётные записи разделены по действиям, поэтому утечка одного пароля не
открывает остальное:

| Учётная запись | Действия | Кто использует |
|---|---|---|
| `publisher` | `publish` | Камеры и другие источники потоков |
| `reader` | `read`, `playback` | Потребители потоков и записей |
| `tacitus-api` | `api` | Бэкенд Tacitus для `GET /streams` |

Публикация и чтение:

```
rtsp://<publisher>:<password>@host:8554/cam1   # публикация
rtsp://<reader>:<password>@host:8554/cam1      # чтение
```

Бэкенд обращается к Control API по HTTP Basic
(`TACITUS_MTX_API_USER` / `TACITUS_MTX_API_PASSWORD`). Отказ по 401/403
логируется как `MediaMTX API rejected backend credentials` и возвращается
клиенту как 502 `MediaMTX API authentication failed` — это признак
рассинхронизации настроек приложения и `authInternalUsers`, а не проблемы
вызывающего.

> ⚠️ **RTSP передаёт Basic-credentials в открытом виде.** `rtspEncryption`
> выключен, поэтому пароль виден на сетевом уровне. Порт `8554` по умолчанию
> публикуется только на loopback (`TACITUS_MTX_RTSP_BIND=127.0.0.1`); для
> камер в сети задайте bind явно **и** ограничьте доступ firewall'ом. За
> пределами доверенного периметра требуется RTSPS или VPN.

> ⚠️ **Пароли обязаны находиться в самом YAML-файле.** Переменные
> `MTX_AUTHINTERNALUSERS_<n>_PASS` не переопределяют запись `<n>` из файла, а
> добавляют новую (проверено на MediaMTX 1.15.1). Production-конфиг поэтому
> рендерится Ansible из `mediamtx.yml.j2` с переменными из vault, а не
> патчится через окружение.

Барьеры против выкатки dev-паролей:

1. `validate_startup_settings()` роняет приложение при
   `TACITUS_ENVIRONMENT=production` и `mtx_api_password = dev-api-change-me`.
2. Pre-check в `ansible/playbook.yml` отказывает в деплое при любом из трёх
   dev-паролей MediaMTX.

Конфигурации: `mediamtx/mediamtx.yml` (dev),
`ansible/roles/mediamtx/templates/mediamtx.yml.j2` (production).
Регрессионные тесты — `tests/test_mediamtx_security.py`,
`tests/test_streams_auth.py`. Решение зафиксировано в ADR-012.

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
- В debug-режиме (`TACITUS_DEBUG=true`) возвращается текст исключения. Поэтому
  при `TACITUS_ENVIRONMENT=production` debug запрещён на старте (§2.6).
- **Ошибки валидации (422) не возвращают присланное значение.** Обработчик
  `validation_error_handler` оставляет только `type`, `loc` и `msg`, удаляя
  поля `input` и `ctx`. По умолчанию FastAPI кладёт в `input` то, что прислал
  клиент, — для `POST /auth/register` и `POST /auth/login` это пароль открытым
  текстом в теле ответа, откуда он попадает в devtools браузера, клиентские
  логи, прокси и трекеры ошибок. Эхо убрано для всех полей, а не по списку
  чувствительных: иначе новое поле с секретом начнёт течь в день его
  добавления.

### 4.4 Скрытие версий ПО

Точная версия компонента сообщает анонимному клиенту, какие опубликованные
уязвимости имеет смысл пробовать. Снаружи доступен только nginx (80/443 —
единственные порты, опубликованные на `0.0.0.0`), поэтому закрываются четыре
канала, проходящие через него:

| Канал | Что раскрывал | Решение |
|---|---|---|
| Заголовок `Server` | `nginx/1.27.5` | `server_tokens off` в `nginx/nginx.conf` и в `nginx-ssl.conf.j2` |
| Подвал штатных страниц ошибок nginx | `nginx/1.27.5` | `error_page` → собственная страница `/error.html` |
| `GET /api/openapi.json`, `/api/docs`, `/api/redoc` | `info.version` и вся поверхность API, анонимно | в production `docs_url`, `redoc_url` и `openapi_url` равны `None` |
| `/assets/*.map` | исходники фронтенда и версии зависимостей | `sourcemap: false` в сборке Vite |

**Заголовок `Server`.** `server_tokens off` убирает номер версии, но не слово
`nginx`. Полное удаление заголовка требует пересборки nginx с модулем
`headers-more` (`more_clear_headers Server`); образ сознательно остаётся
стоковым `nginx:alpine`, поэтому принято скрытие версии, а не факта.

**Страницы ошибок.** `server_tokens off` вычищает версию и из подвала штатных
страниц, но имя сервера там остаётся, поэтому страницы заменены целиком.
Перехват касается только тех кодов, которые nginx формирует сам;
`proxy_intercept_errors` выключен, и JSON-ответы приложения проходят без
подмены.

**Интерактивная документация.** nginx проксирует `/api/` на корень приложения
(`proxy_pass http://app:8000/;` с завершающим слэшем), поэтому дефолтные
`/docs`, `/redoc` и `/openapi.json` FastAPI отвечали снаружи без
аутентификации — то есть версия уходила анонимному клиенту мимо
`GET /settings/version`, который держит её за токеном. Решение принимает
`app/core/config.py::api_docs_enabled()`; все три URL гасятся одним условием,
потому что оставить `openapi_url` при выключенном `docs_url` — значит спрятать
просмотрщик, а не документ. Вне production документация работает как прежде.

Единственный канал получения версии снаружи — `GET /settings/version`
(аутентификация, 60/мин). Регрессионные тесты — `tests/test_nginx_security.py`
и `tests/test_api_docs_exposure.py`.

Не затрагиваются компоненты, недоступные снаружи по топологии: Control API
MediaMTX (только внутренняя сеть), MinIO (порты не публикуются вовсе),
PostgreSQL и Redis. RTSP по умолчанию слушает loopback.

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
| `auth.register`               | info     | ip, user (без `ip` при создании через `scripts/init_project.py`) |
| `auth.refresh.success`        | info     | ip, user                  |
| `auth.refresh.reuse_detected` | warning  | user                      |
| `auth.token.revoked`          | info     | ip, user, refresh_revoked |
| `auth.logout.foreign_refresh_token` | warning | user, token_owner |
| `file.upload`                 | info     | ip, user, file_name, size |
| `file.download`               | info     | ip, user, file_name       |
| `file.delete`                 | info     | ip, user, file_name       |
| `recording.download`          | info     | ip, user, object_key      |
| `recording.delete`            | info     | ip, user, object_key      |
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

### 7.5 Host Firewall & Patching

Container hardening (7.1–7.4) protects what runs inside Docker. The host it
runs on is a separate surface, and `docker-compose.yml`'s port bindings do
not defend it: a bind to `127.0.0.1` keeps a port off the network regardless
of any host firewall, but nothing there stops an operator — or a later
change — from widening `TACITUS_MTX_RTSP_BIND` to `0.0.0.0` without a
firewall rule to match. Two Ansible roles cover the host itself, both on by
default:

- **`firewall`** (`ansible/roles/firewall`) installs `ufw`, sets default-deny
  on incoming connections, and opens exactly what `docker-compose.yml`
  actually publishes to `0.0.0.0`: SSH (whatever port Ansible is already
  using — never a hardcoded 22 that could lock out a customized setup) and
  `80`/`443`. It runs *before* `ssl_cert` (Let's Encrypt's webroot challenge
  needs port 80 reachable) and *before* `app_deploy` (the host is closed
  before anything starts listening on it). A `wait_for_connection` check
  right after enabling the firewall catches a lockout — wrong SSH port,
  typo in the rule — while the play can still fail loudly instead of the
  next `ansible-playbook` run just timing out.
- **`unattended_upgrades`** (`ansible/roles/unattended_upgrades`) installs
  `unattended-upgrades` and enables the daily security-update cycle. It
  deliberately does not redefine which origin counts as "security" — that
  stays whatever the distribution's own package ships, rather than a
  second definition that could drift from it. Automatic **reboot is off by
  default**: this host runs PostgreSQL and, when MediaMTX is actively
  recording, live RTSP sessions, and an unplanned reboot mid-recording is a
  worse outcome than a delayed kernel update. Patches to already-running
  packages still apply without a reboot.

Both are Debian/Ubuntu-only (matching `docker_install`'s existing
assumption) and toggle off independently: `-e firewall_enabled=false` or
`-e unattended_upgrades_enabled=false`, e.g. for a host whose firewalling
already happens at the cloud provider's security-group layer.

---

## 8. Dependency & Supply Chain Security

### 8.1 Dependency Scanning

- Зависимости зафиксированы с SHA-256 хешами в `requirements.txt`,
  сгенерированном из `uv.lock` командой `uv export`.
- **Docker-образ собирается именно по этому файлу:**
  `pip install --require-hashes -r requirements.txt`. Несовпадение хеша или
  пакет, отсутствующий в лок-файле, роняют сборку.
- Приложение не устанавливается как дистрибутив — каталог `app/` копируется в
  образ. Установка запускала бы PEP 517 source build, который тянет
  `setuptools` из PyPI мимо хеш-пиннинга.

> До 2026-09-04 это утверждение не соответствовало действительности:
> `Dockerfile` ставил зависимости из `pyproject.toml` с диапазонами `>=`, а
> `requirements.txt` в сборке не участвовал и успел разойтись с проектом —
> в нём отсутствовали `sqlalchemy`, `uvicorn`, `python-jose` и `sentry-sdk`.
> Расхождение было видно по версиям: лок-файл фиксировал `boto3 1.43.24`, в
> образе стояла `1.43.87`.

Сканирование уязвимостей запускается **вручную** — процедура и разбор вывода
в [`docs/audit.md`](audit.md). Коротко:

```bash
pip-audit -r requirements.txt
cd frontend && npm audit
```

> Хуки `pip-audit` и `npm audit` закомментированы в `.pre-commit-config.yaml`
> намеренно: они ходят в сеть, добавляют около 40 секунд к коммиту и
> срабатывают на появление нового CVE, а не на изменение кода. Активные хуки —
> базовые проверки `pre-commit-hooks`, `ruff`, `ruff-format` и `mypy`.

Обновление lock-файла (подробнее — `docs/uv.md`):

```bash
uv lock
uv export --frozen --no-dev --no-emit-project \
  --format requirements-txt --output-file requirements.txt
```

#### Результат аудита от 2026-09-05

`pip-audit -r requirements.txt` — **1 известная уязвимость в 1 пакете**.

| Пакет | Версия | ID | Fix | Применимость к Tacitus |
|---|---|---|---|---|
| `ecdsa` | 0.19.2 | PYSEC-2026-1325 | **нет** | Принятый риск. Minerva timing attack на P-256; приходит транзитивно через `python-jose`. Tacitus подписывает JWT алгоритмом HS256 (HMAC), ECDSA-путь не задействован |

**Frontend (`npm audit`, 2026-09-05): 0 уязвимостей.**

Найденные в этот день 4 уязвимости (3 высокой критичности) закрыты в тот же
день через `npm audit fix` — все исправления уложились в существующие
`^`-ограничения `package.json`, менять его не потребовалось:

| Пакет | Было | Стало | Что закрыто |
|---|---|---|---|
| `react-router` | 7.17.0 | 7.18.3 | Unauthenticated DoS через роут-матчинг, open redirect, XSS в `RSCErrorHandler`, обход CSRF |
| `react-router-dom` | 7.17.0 | 7.18.3 | Зависимость от уязвимой версии `react-router` |
| `nanoid` | 3.3.12 | 3.3.18 | Бесконечный цикл в генераторах |
| `postcss` | 8.5.15 | 8.5.28 | Path traversal при автозагрузке source map |

Процедура — `docs/audit.md` → Frontend.

Предыдущий аудит (2026-09-03) находил **14 уязвимостей в 5 пакетах**. Они
закрылись при перегенерации лок-файла из `uv.lock`, потому что старый
`requirements.txt` фиксировал давно устаревшие версии:

| Пакет | Было | Стало | Что закрыто |
|---|---|---|---|
| `starlette` | 1.2.1 | 1.6.0 | PYSEC-2026-248, PYSEC-2026-249 (`request.form()` не применял `max_fields`/`max_part_size` — касалось `POST /files/upload`) |
| `cryptography` | 48.0.0 | 48.0.1+ | GHSA-537c-gmf6-5ccf и три PYSEC по цепочкам сертификатов и PKCS#7 |
| `pyasn1` | 0.6.3 | 0.6.4 | PYSEC-2026-3455/3456/3457 |
| `pydantic-settings` | 2.14.1 | 2.14.2+ | GHSA-4xgf-cpjx-pc3j |

Аудит проверяет `requirements.txt` — тот же файл, по которому собирается
образ, поэтому проверенные версии совпадают с установленными.

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
- [x] Версия nginx не раскрывается ни в заголовке `Server`, ни в теле страниц ошибок (`server_tokens off` + `error_page`)
- [x] `/api/openapi.json`, `/api/docs` и `/api/redoc` недоступны в production
- [x] Source maps фронтенда не попадают в образ (`sourcemap: false`)

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

- [ ] `TACITUS_ENVIRONMENT=production` — этим же значением выключается интерактивная документация API
- [ ] `TACITUS_DEBUG=false`
- [ ] `TACITUS_JWT_SECRET` — минимум 32 символа, не дефолт
- [ ] MediaMTX credentials (`mediamtx_publish_password`, `mediamtx_read_password`,
      `mediamtx_api_password`) — не dev-плейсхолдеры
- [ ] RTSP-порт `8554` привязан к доверенному интерфейсу (по умолчанию —
      loopback) **и** закрыт хостовым firewall'ом — роль `firewall` открывает
      только 22/80/443, поэтому по умолчанию 8554 недоступен извне без
      дополнительного действия на обоих уровнях
- [ ] Хостовый firewall (`ufw`) активен: `ansible-playbook ... --tags firewall`
      либо проверить вручную `ufw status`
- [ ] Автообновления безопасности хоста включены (`unattended-upgrades`) —
      актуально именно потому, что образы приложения закреплены точными
      патч-тегами и сами не обновляются (см. `docs/audit.md`)
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
