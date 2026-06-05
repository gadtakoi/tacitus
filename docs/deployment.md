# Деплой

## Локальный запуск

```bash
docker compose up -d
```

## Ansible

Плейбук: `ansible/playbook.yml`

Роли:

- `docker_install` — установка Docker Engine
- `ssl_cert` — установка и получение SSL-сертификата Let's Encrypt (опционально)
- `app_deploy` — клонирование репозитория, `docker compose up`
- `nginx_ssl` — генерация nginx конфигурации с HTTPS (опционально)

```bash
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbook.yml
```

## SSL / Let's Encrypt (Certbot)

Для production-деплоя с HTTPS используется **Certbot** в standalone-режиме.

### Переменные (ansible/group_vars/all.yml)

| Переменная            | Значение по умолчанию | Описание                                    |
|-----------------------|-----------------------|---------------------------------------------|
| `nginx_ssl_enabled`   | `false`               | Включить HTTPS                              |
| `nginx_domain`        | `tacitus.example.com` | Домен для сертификата                       |
| `certbot_admin_email` | `admin@example.com`   | Email для Let's Encrypt                     |
| `certbot_staging`     | `true`                | Использовать staging окружение (для тестов) |

### Как это работает

1. **Условное включение**: роли `ssl_cert` и `nginx_ssl` выполняются только если `nginx_ssl_enabled: true`.
2. **Получение сертификата**: certbot в standalone-режиме временно занимает порт 80, проходит ACME-валидацию и сохраняет
   сертификаты в `/etc/letsencrypt/live/{{ nginx_domain }}/`.
3. **Nginx конфигурация**: шаблон `ansible/roles/nginx_ssl/templates/nginx.conf.j2` генерирует:
    - Редирект с HTTP (80) → HTTPS (443) с кодом 301
    - HTTPS-сервер с `listen 443 ssl http2`, TLS 1.2/1.3, строгими шифрами
    - Пути к `fullchain.pem` и `privkey.pem`
4. **Docker volume**: сертификаты монтируются в контейнер nginx через bind mount:
   ```yaml
   volumes:
     - /etc/letsencrypt:/etc/letsencrypt:ro
   ```
5. **Автообновление**: cron задача запускается каждый день в 3:00:
   ```
   certbot renew --quiet --deploy-hook 'docker compose ... exec nginx nginx -s reload'
   ```
   После успешного обновления nginx получает сигнал reload.

### Первый запуск (staging → production)

1. Убедитесь, что DNS A-запись домена указывает на сервер.
2. Установите `certbot_staging: true` и запустите плейбук — проверьте, что certbot отрабатывает без ошибок.
3. Переключите `certbot_staging: false` и запустите повторно — получите валидный сертификат.

### Ограничения standalone-режима

Standalone требует временного занятия порта 80. Если на сервере уже работает nginx, используйте `--webroot` (требует
`location /.well-known/acme-challenge/` в конфиге) или временно остановите nginx перед выпуском сертификата.

## Переменные окружения (production)

Все dev defaults в `app/core/config.py` должны быть переопределены:

```bash
TACITUS_JWT_SECRET=<strong-random-secret>
TACITUS_S3_ACCESS_KEY=<minio-access-key>
TACITUS_S3_SECRET_KEY=<minio-secret-key>
```

- Секреты хранить только в утверждённом secret manager (например, HashiCorp Vault, AWS Secrets Manager, или переменные окружения хоста)
- Никогда не коммитить `.env` (уже в `.gitignore`)
- Не логировать секреты
