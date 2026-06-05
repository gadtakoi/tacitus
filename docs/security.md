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
- Все cookies (при использовании helper `app.core.security.set_secure_cookie`) получают флаги `Secure`, `HttpOnly`,
  `SameSite=Strict`.

---

## 2. Authentication & Authorization Hardening

### 2.1 Rate Limiting

Установлен `slowapi` с in-memory storage. Лимиты:

| Endpoint              | Лимит  | Ключ         |
|-----------------------|--------|--------------|
| `POST /auth/login`    | 5/мин  | IP           |
| `POST /auth/register` | 3/час  | IP           |
| `POST /files/upload`  | 10/мин | пользователь |
| `GET /files/`         | 60/мин | пользователь |

При превышении возвращается `429 Too Many Requests`.

### 2.2 Brute Force Protection

- In-memory счётчик неудачных попыток входа с окном 15 минут.
- Блокировка аккаунта после 5 неудачных попыток подряд.
- Все неудачные попытки логируются с `username` и `client_ip`.

### 2.3 Password Policy

Пароль при регистрации должен соответствовать правилам:

- Минимум 8 символов
- Хотя бы одна заглавная буква
- Хотя бы одна строчная буква
- Хотя бы одна цифра
- Хотя бы один специальный символ

Валидация реализована через Pydantic `field_validator` в `RegisterRequest`.

### 2.4 Token Management

- Access token: TTL **15 минут**.
- Refresh token: TTL **7 дней**, хранится в in-memory store.
- `POST /auth/refresh` — обмен refresh token на новый access token.
- `POST /auth/logout` — добавление access token в blacklist.
- `decode_access_token()` проверяет blacklist перед валидацией.

### 2.5 Startup Secret Validation

При `TACITUS_ENVIRONMENT=production` приложение падает с `RuntimeError`, если:

- `jwt_secret` короче 32 символов
- `jwt_secret == "change-me-in-production"`
- S3 credentials == `minioadmin`
- `cors_origins` содержит `*`

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

### 5.1 Remove Dev Defaults from Code

В `app/core/config.py` удалены dev defaults для чувствительных переменных:

- `s3_access_key: str = Field(..., min_length=8)`
- `s3_secret_key: str = Field(..., min_length=8)`
- `jwt_secret: str = Field(...)`

Dev-значения для локальной разработки указаны в `.env.example`.

---

## 6. Audit Logging & Monitoring

### 6.1 Security Events Logging

Dedicated security logger (`app.core.security_logging`) фиксирует события:

| Событие               | severity | поля                      |
|-----------------------|----------|---------------------------|
| `auth.login.success`  | info     | ip, user                  |
| `auth.login.failed`   | warning  | ip, user                  |
| `auth.register`       | info     | ip, user                  |
| `auth.token.revoked`  | info     | ip, user                  |
| `file.upload`         | info     | ip, user, file_name, size |
| `file.delete`         | info     | ip, user, file_name       |
| `rate_limit.exceeded` | warning  | ip, endpoint              |

### 6.2 Suspicious Activity Detection

`SuspiciousActivityMiddleware` отслеживает 401/403 ответы по IP:

- При >100 запросов за 5 минут логируется `security.suspicious_activity` (warning).

---

## 7. Container & Infrastructure Security

### 7.1 Non-root Containers

- **App:** `USER tacitus` (uid 1000) в `Dockerfile`
- **Nginx:** `user nginx;` в `nginx/nginx.conf` (worker processes от непривилегированного пользователя, master process от root для управления портами и privileges)

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

- **Python:** `pip-audit` сканирует `requirements.txt` в pre-commit
- **Frontend:** `npm audit --audit-level=moderate` в pre-commit
- Зависимости зафиксированы с SHA-256 хешами в `requirements.txt`

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
- [ ] Rate limiting активен
- [ ] Non-root контейнеры
- [ ] `read_only: true` для app и nginx
- [ ] `.env` недоступен через nginx

---
