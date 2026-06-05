# Security

## Dependency & Supply Chain Security

### Dependency Locking

Проект использует **pip-tools** для воспроизводимой установки зависимостей.

- Исходные зависимости описаны в `pyproject.toml`.
- Зафиксированные зависимости с SHA-256 хешами хранятся в `requirements.txt`.
- Обновление lock-файла:

```bash
# Установить pip-tools
pip install pip-tools

# Перегенерировать requirements.txt с хешами
pip-compile --generate-hashes pyproject.toml -o requirements.txt
```

### Dependency Scanning

#### Python — pip-audit

В `pyproject.toml` добавлен `pip-audit` в dev-зависимости. В `pre-commit` настроен hook, который сканирует `requirements.txt` на наличие известных уязвимостей:

```bash
# Ручной запуск
python -m pip_audit -r requirements.txt
```

#### Frontend — npm audit

В `.pre-commit-config.yaml` добавлен hook для `npm audit`. Frontend зависимости сканируются при каждом коммите:

```bash
cd frontend
npm audit --audit-level=moderate
```

### SBOM (Software Bill of Materials)

Для генерации SBOM используются:

- **Python:** `cyclonedx-bom` (CycloneDX)
- **Frontend:** `@cyclonedx/cyclonedx-npm`

Единый скрипт генерации:

```bash
python scripts/generate-sbom.py
```

Результат:

- `sbom-python.json` — SBOM для Python-зависимостей
- `frontend/sbom.json` — SBOM для frontend-зависимостей

Артефакты SBOM не коммитятся в репозиторий. Они генерируются при подготовке релиза и прикрепляются к нему.

---

## Deployment Security Checklist

Перед деплоем в production:

- [ ] `TACITUS_ENVIRONMENT=production` установлена
- [ ] `TACITUS_JWT_SECRET` — минимум 32 символа, не дефолтное значение
- [ ] `TACITUS_S3_ACCESS_KEY` и `TACITUS_S3_SECRET_KEY` — не `minioadmin`
- [ ] HTTPS включён (Let's Encrypt или собственный сертификат)
- [ ] Security headers настроены в nginx (HSTS, CSP, X-Frame-Options и др.)
- [ ] CORS origins не содержат `*`
- [ ] Rate limiting активен
- [ ] Контейнеры запускаются от non-root пользователей
- [ ] `read_only: true` для сервисов app и nginx
- [ ] `.env` недоступен через nginx

---

## Secret Rotation

### JWT Secret

Ротация `jwt_secret` без downtime:

1. Сгенерировать новый секрет (минимум 32 символа):
   ```bash
   openssl rand -base64 48
   ```
2. Обновить `TACITUS_JWT_SECRET` в Vault/окружении.
3. Перезапустить инстансы приложения по очереди (rolling restart).
   - Старые токены, выданные до ротации, станут невалидны сразу после перезапуска.
   - Для безостановочной ротации можно реализовать поддержку нескольких signing keys (не входит в MVP).
4. Убедиться, что пользователи могут получить новые токены через `/auth/login` или `/auth/refresh`.

### S3 Credentials

Ротация S3 credentials:

1. Создать новую пару access key / secret key в MinIO/AWS IAM.
2. Обновить `S3_ACCESS_KEY` и `S3_SECRET_KEY` в окружении.
3. Перезапустить приложение (rolling restart).
4. Удалить старую пару ключей из S3-провайдера после проверки работоспособности.

---

## Incident Response

### Компрометация токена

Если подтверждена утечка access token:

1. Вызвать `POST /auth/logout` с данным токеном — он будет добавлен в blacklist.
2. При массовой компрометации перегенерировать `jwt_secret` (см. Secret Rotation).
3. Проинформировать пользователей о необходимости повторного входа.
4. Проверить логи на предмет аномальной активности.

### Контакты

- **Security Team:** security@example.com
- **On-call:** +1-555-SECURITY
- **Escalation:** CTO / DevOps lead

> **Примечание:** замените контакты на реальные перед использованием в production.
