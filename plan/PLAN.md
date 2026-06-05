# План: Усиление безопасности (Security Hardening)

## 1. Transport Layer Security

### 1.1. Security Headers в Nginx

- [x] Добавить `Strict-Transport-Security` (HSTS) в dev-конфиг `nginx/nginx.conf`:
  ```nginx
  add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
  ```
- [x] Добавить `Content-Security-Policy` (CSP) для защиты от XSS и data injection:
  ```nginx
  add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'" always;
  ```
- [x] Добавить `X-XSS-Protection` (legacy fallback):
  ```nginx
  add_header X-XSS-Protection "1; mode=block" always;
  ```
- [x] Добавить `Permissions-Policy` для ограничения browser features:
  ```nginx
  add_header Permissions-Policy "geolocation=(), microphone=(), camera=()" always;
  ```
- [x] Обновить Ansible шаблон `ansible/roles/ssl_cert/templates/nginx-ssl.conf.j2` с теми же заголовками

### 1.2. HTTPS Enforcement

- [x] Добавить `upgrade-insecure-requests` в CSP для production
- [x] Убедиться, что все cookies (если появятся) имеют флаги `Secure`, `HttpOnly`, `SameSite=Strict`

---

## 2. Authentication & Authorization Hardening

### 2.1. Rate Limiting

- [x] Установить `slowapi` для rate limiting
- [x] Настроить лимиты:
  - `/auth/login` — 5 попыток в минуту с одного IP
  - `/auth/register` — 3 попытки в час с одного IP
  - `/files/upload` — 10 загрузок в минуту на пользователя
  - `/files/` (GET) — 60 запросов в минуту на пользователя
- [x] Вернуть `429 Too Many Requests` при превышении лимита
- [x] Добавить тесты на rate limiting

### 2.2. Brute Force Protection

- [x] Добавить счётчик неудачных попыток входа в `auth_service.py` (in-memory с TTL)
- [x] Временно блокировать учётную запись после 5 неудачных попыток подряд (15 минут)
- [x] Логировать все неудачные попытки входа с IP-адресом

### 2.3. Password Policy

- [x] Добавить валидацию пароля при регистрации:
  - Минимум 8 символов
  - Хотя бы одна заглавная буква
  - Хотя бы одна строчная буква
  - Хотя бы одна цифра
  - Хотя бы один специальный символ
- [x] Добавить Pydantic валидатор для `password` в `RegisterRequest` schema
- [x] Обновить тесты регистрации (валидные/невалидные пароли)

### 2.4. Token Management

- [x] Добавить refresh tokens (с TTL 7 дней, хранить в in-memory store)
- [x] Реализовать endpoint `POST /auth/refresh`
- [x] Реализовать endpoint `POST /auth/logout` (добавление access token в blacklist)
- [x] Добавить проверку blacklist в `decode_access_token()`
- [x] Установить `jwt_expiration_minutes: int = 15` (сократить с 30 до 15 минут)

### 2.5. Startup Secret Validation

- [x] Добавить валидацию `settings.jwt_secret` при старте приложения:
  - Минимальная длина 32 символа
  - Запретить дефолтное значение `"change-me-in-production"` в production режиме
  - Запретить дефолтные S3 credentials (`minioadmin`) в production режиме
- [x] Добавить `ENVIRONMENT` переменную (`development` / `production`)
- [x] При `ENVIRONMENT=production` и слабом секрете — падать с `RuntimeError` и понятным сообщением

---

## 3. Input Validation & File Upload Security

### 3.1. Filename Sanitization

- [x] Добавить функцию `sanitize_filename()` в `app/services/s3_service.py`:
  - Удалить path traversal (`../`, `..\\`, нулевые байты)
  - Разрешить только `a-zA-Z0-9._-`
  - Ограничить длину до 255 символов
  - Запретить имена, начинающиеся с точки (hidden files)
- [x] Применять sanitization во всех методах, принимающих filename
- [x] Вернуть `400 Bad Request` при некорректном имени файла

### 3.2. File Type Validation

- [x] Добавить whitelist разрешённых MIME-типов:
  ```python
  ALLOWED_TYPES = {
      "image/jpeg", "image/png", "image/gif", "image/webp",
      "application/pdf", "text/plain", "text/markdown",
      "application/json", "application/zip",
  }
  ```
- [x] Проверять `file.content_type` при загрузке
- [ ] Опционально: проверять magic bytes через `python-magic`
- [x] Вернуть `415 Unsupported Media Type` при запрещённом типе

### 3.3. File Extension Validation

- [x] Проверять расширение файла на соответствие MIME-типу
- [x] Запретить исполняемые расширения: `.exe`, `.bat`, `.sh`, `.py`, `.js`, `.php`
- [x] Добавить тесты на загрузку опасных файлов

---

## 4. CORS & API Security

### 4.1. CORS Hardening

- [ ] Заменить `allow_origins=["*"]` на configurable список:
  ```python
  allow_origins=["http://localhost:3000", "https://{{ nginx_domain }}"]
  ```
- [ ] При `ENVIRONMENT=production` запретить `*` в origins
- [ ] Вынести `CORS_ORIGINS` в `Settings`

### 4.2. Request Limits

- [ ] Установить `max_request_size` для FastAPI (через middleware или nginx `client_max_body_size`)
- [ ] Добавить `client_max_body_size 11M;` в nginx (чуть больше лимита приложения)
- [ ] Добавить `client_body_timeout 30s;` и `client_header_timeout 10s;` в nginx

### 4.3. Error Handling

- [ ] Убедиться, что production не возвращает stack trace клиенту
- [ ] Убедиться, что `detail` ошибок не раскрывает внутренней структуры (SQL, пути)
- [ ] Добавить generic `500` ответ с safe message для production

---

## 5. Secrets Management

### 5.1. Remove Dev Defaults from Code

- [ ] Удалить dev defaults для чувствительных переменных из `app/core/config.py`:
  ```python
  # Было:
  s3_access_key: str = "minioadmin"
  # Стало:
  s3_access_key: str = Field(..., min_length=8)
  ```
- [ ] Сделать `jwt_secret` обязательным без дефолта
- [ ] Обновить `.env.example` с явными dev-значениями для локальной разработки

### 5.2. Vault Integration Path

- [ ] Добавить `app/core/vault.py` — адаптер для HashiCorp Vault (stub/интерфейс)
- [ ] При `VAULT_ADDR` определённом — загружать секреты из Vault вместо env vars
- [ ] Логировать факт обращения к Vault без раскрытия значений (согласно `.ai/modules/vault.md`)

---

## 6. Audit Logging & Monitoring

### 6.1. Security Events Logging

- [ ] Добавить dedicated security logger (`app.core.security_logging`)
- [ ] Логировать события:
  - `auth.login.success` / `auth.login.failed` (username, IP, user-agent)
  - `auth.register` (username, IP)
  - `auth.token.revoked` (username)
  - `file.upload` (filename, size, user)
  - `file.delete` (filename, user)
  - `rate_limit.exceeded` (endpoint, IP)
- [ ] Формат: JSON structured logging с полями `event_type`, `severity`, `ip`, `user`

### 6.2. Suspicious Activity Detection

- [ ] Добавить in-memory счётчик подозрительных запросов по IP
- [ ] При >100 401/403 ответов за 5 минут — логировать `security.suspicious_activity`
- [ ] (Future) Интеграция с fail2ban или WAF

---

## 7. Container & Infrastructure Security

### 7.1. Non-root Containers

- [ ] Добавить `USER` в `Dockerfile` (app) — создать unprivileged user `tacitus`
- [ ] Добавить `USER` в `nginx/Dockerfile` — использовать `nginx` user
- [ ] Убедиться, что приложение запускается от непривилегированного пользователя

### 7.2. Read-only Root Filesystem

- [ ] Добавить `read_only: true` в `docker-compose.yml` для сервиса `app`
- [ ] Добавить tmpfs volume для `/tmp` если нужно
- [ ] Добавить `read_only: true` для `nginx` (с volume для `/var/cache/nginx`, `/var/log/nginx`)

### 7.3. Capability Dropping

- [ ] Добавить в `docker-compose.yml`:
  ```yaml
  cap_drop:
    - ALL
  cap_add:
    - NET_BIND_SERVICE  # только для nginx если нужно
  ```

### 7.4. Resource Limits

- [ ] Добавить `deploy.resources` в `docker-compose.yml`:
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

### 8.1. Dependency Scanning

- [ ] Добавить `pip-audit` или `safety` в pre-commit / CI
- [ ] Добавить `npm audit` для frontend в pre-commit
- [ ] Зафиксировать все зависимости с hash (`requirements.txt` с `--hash=sha256:` или `uv.lock`)

### 8.2. SBOM

- [ ] Добавить генерацию SBOM в CI: `cyclonedx-py` для Python, `@cyclonedx/cyclonedx-npm` для frontend
- [ ] Хранить SBOM артефакты рядом с релизами

---

## 9. Security Testing

### 9.1. Security-focused Unit Tests

- [ ] `test_password_policy_validation` — слабые/сильные пароли
- [ ] `test_filename_path_traversal` — `../../../etc/passwd`
- [ ] `test_file_type_rejection` — загрузка `.exe` под видом `.txt`
- [ ] `test_rate_limit_blocks_after_threshold` — 429 response
- [ ] `test_brute_force_lockout` — блокировка после 5 попыток
- [ ] `test_token_blacklist` — revoked token возвращает 401
- [ ] `test_cors_rejects_unknown_origin` — preflight failed

### 9.2. Security Headers Verification

- [ ] Добавить тест, проверяющий наличие security headers в ответе nginx
- [ ] Проверить HSTS, CSP, X-Frame-Options, X-Content-Type-Options

### 9.3. Penetration Testing Checklist

- [ ] Проверить отсутствие stack trace в production-режиме
- [ ] Проверить, что `.env` недоступен через nginx (`location /.env { deny all; }`)
- [ ] Проверить, что MinIO console недоступна извне
- [ ] Проверить заголовки через `securityheaders.com` или `curl -I`

---

## 10. Documentation & Runbooks

### 10.1. Security Runbook

- [ ] Добавить `docs/security.md`:
  - Чек-лист деплоя (secrets, HTTPS, headers)
  - Как ротировать JWT secret
  - Как реагировать на compromise (token revocation)
  - Контакты security team / процедура incident response

### 10.2. Secret Rotation Guide

- [ ] Описать процесс ротации `jwt_secret` без downtime
- [ ] Описать процесс ротации S3 credentials

---

## 11. Финальная верификация

- [ ] `pytest tests/` — все passed (включая новые security тесты)
- [ ] `ruff check .` — passed
- [ ] `pip-audit` — 0 критических уязвимостей
- [ ] `npm audit` (frontend) — 0 критических уязвимостей
- [ ] Security headers проверены через `curl -I` на всех endpoints
- [ ] Контейнеры запускаются от non-root
- [ ] Docker Compose проходит `docker compose config` без ошибок
