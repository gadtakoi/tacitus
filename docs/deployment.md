# Деплой

## Локальный запуск

```bash
docker compose up -d
```

---

## Развёртывание через Ansible

Ansible-плейбук автоматически устанавливает Docker, копирует код проекта на сервер, создаёт файл окружения `.env` и
запускает приложение через `docker compose`. При необходимости получает SSL-сертификат Let's Encrypt и настраивает
HTTPS.

### Требования

**На управляющей машине (ваш ПК):**

- Python 3.10+
- Ansible 2.14+

**На целевом сервере:**

- Ubuntu 20.04+ / Debian 11+ (роль `docker_install` заточена под `apt`)
- SSH-доступ с правами `sudo` (безпарольный `sudo` или пароль через `--ask-become-pass`)
- Открытые порты: 80 и (опционально) 443, а также настраиваемый RTSP-порт MediaMTX (по умолчанию 8554)

### Установка Ansible

```bash
pip install ansible
```

### Проверка доступности хоста

```bash
cd ansible
ansible -i inventory/hosts.yml tacitus -m ping \
  -e target_host=1.2.3.4 \
  -e deploy_user=root
```

Если подключаетесь по паролю:

```bash
ansible -i inventory/hosts.yml tacitus -m ping \
  -e target_host=1.2.3.4 \
  -e deploy_user=root \
  -e deploy_password=your_password
```

### Базовый деплой (HTTP, без домена)

Минимальная команда — только IP, логин и пароль:

```bash
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbook.yml \
  -e target_host=1.2.3.4 \
  -e deploy_user=root \
  -e deploy_password=your_password
```

Если на сервере `sudo` требует пароль:

```bash
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbook.yml \
  -e target_host=1.2.3.4 \
  -e deploy_user=ubuntu \
  -e deploy_password=your_password \
  --ask-become-pass
```

Если используете SSH-ключ вместо пароля:

```bash
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbook.yml \
  -e target_host=1.2.3.4 \
  -e deploy_user=root \
  --private-key ~/.ssh/id_rsa
```

### Деплой с HTTPS (домен + SSL)

Добавьте доменное имя и включите HTTPS:

```bash
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbook.yml \
  -e target_host=1.2.3.4 \
  -e deploy_user=root \
  -e deploy_password=your_password \
  -e tacitus_enable_https=true \
  -e tacitus_domain=example.com \
  -e tacitus_email=admin@example.com
```

**Важно:** до запуска убедитесь, что DNS A-запись `example.com` указывает на IP сервера (`1.2.3.4`).

#### Staging → Production сертификат

Let's Encrypt имеет лимиты на количество запросов. Для первых тестов рекомендуется staging:

```bash
# 1. Тестовый сертификат (staging)
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbook.yml \
  -e target_host=1.2.3.4 \
  -e deploy_user=root \
  -e deploy_password=your_password \
  -e tacitus_enable_https=true \
  -e tacitus_domain=example.com \
  -e ssl_certbot_staging=true

# 2. Если всё ок — получаем реальный сертификат
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbook.yml \
  -e target_host=1.2.3.4 \
  -e deploy_user=root \
  -e deploy_password=your_password \
  -e tacitus_enable_https=true \
  -e tacitus_domain=example.com \
  -e ssl_certbot_staging=false
```

### Передача секретов

По умолчанию `.env.j2` использует dev-значения. **В production их обязательно переопределяйте:**

```bash
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbook.yml \
  -e target_host=1.2.3.4 \
  -e deploy_user=root \
  -e deploy_password=your_password \
  -e tacitus_jwt_secret="$(openssl rand -hex 32)" \
  -e tacitus_s3_access_key="$(openssl rand -hex 16)" \
  -e tacitus_s3_secret_key="$(openssl rand -hex 32)"
```

> ⚠️ **Никогда не коммитьте пароли в репозиторий.** Передавайте секреты через `-e` прямо в командной строке (для разовых деплоев) или через переменные окружения управляющей машины:
>
> ```bash
> ansible-playbook -i ansible/inventory/hosts.yml ansible/playbook.yml \
>   -e target_host=1.2.3.4 \
>   -e deploy_user=root \
>   -e deploy_password=your_password \
>   -e tacitus_jwt_secret="$(openssl rand -hex 32)" \
>   -e tacitus_s3_access_key="your-s3-key" \
>   -e tacitus_s3_secret_key="your-s3-secret"
> ```

### Полный пример production-деплоя

Всё вместе — IP, логин, SSH-пароль, домен, HTTPS и staging-сертификат:

```bash
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbook.yml \
  -e target_host=1.2.3.4 \
  -e deploy_user=root \
  -e deploy_password=your_password \
  -e tacitus_enable_https=true \
  -e tacitus_domain=example.com \
  -e tacitus_email=admin@example.com \
  -e ssl_certbot_staging=true
```

После успешного staging-прогона перезапустите с `ssl_certbot_staging=false`, чтобы получить валидный сертификат.

### Переменные плейбука

| Переменная              | По умолчанию              | Описание                              |
|-------------------------|---------------------------|---------------------------------------|
| `target_host`           | `localhost`               | IP или домен целевого сервера         |
| `deploy_user`           | `ansible`                 | SSH-пользователь                      |
| `deploy_password`       | —                         | SSH-пароль (если не используете ключ) |
| `tacitus_enable_https`  | `false`                   | Включить HTTPS и Let's Encrypt        |
| `tacitus_domain`        | `""`                      | Домен для сертификата                 |
| `tacitus_email`         | `admin@example.com`       | Email для Let's Encrypt               |
| `ssl_certbot_staging`   | `false`                   | Staging-режим Certbot                 |
| `tacitus_jwt_secret`    | `change-me-in-production` | JWT-секрет приложения                 |
| `tacitus_s3_access_key` | `minioadmin`              | S3 access key                         |
| `tacitus_s3_secret_key` | `minioadmin`              | S3 secret key                         |
| `tacitus_deploy_dir`    | `/opt/tacitus`            | Путь установки на сервере             |

Полный список — в файлах `ansible/group_vars/all.yml` и `ansible/roles/*/defaults/main.yml`.

### Выборочный запуск (теги)

| Тег        | Что делает                                       |
|------------|--------------------------------------------------|
| `docker`   | Только установка Docker                          |
| `ssl`      | Только получение/обновление SSL                  |
| `app`      | Только копирование кода и перезапуск контейнеров |
| `mediamtx` | Только деплой MediaMTX конфигурации и запуск     |

Пример — обновить только код без переустановки Docker:

```bash
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbook.yml \
  -e target_host=1.2.3.4 \
  -e deploy_user=root \
  -e deploy_password=your_password \
  --tags app
```

### Обновление приложения

Повторный запуск того же плейбука — идемпотентен. Он скопирует изменения из репозитория и перезапустит контейнеры:

```bash
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbook.yml \
  -e target_host=1.2.3.4 \
  -e deploy_user=root \
  -e deploy_password=your_password
```

### Структура ролей

- **`docker_install`** — устанавливает Docker Engine и Docker Compose plugin (только Debian/Ubuntu), добавляет
  пользователя в группу `docker`.
- **`ssl_cert`** — устанавливает Certbot, получает SSL-сертификат через webroot-режим, деплоит nginx-конфиг с HTTPS,
  настраивает автообновление по cron.
- **`app_deploy`** — создаёт директорию `/opt/tacitus`, копирует исходный код, Dockerfile, docker-compose.yml, рендерит
  `.env` из шаблона, запускает `docker compose up --build -d`.
- **`mediamtx`** — создаёт директорию `./recordings` с владельцем `1000:1000`, рендерит `mediamtx.yml` из шаблона,
  запускает/перезапускает контейнер `mediamtx`, выполняет pre-checks (доступность Docker, свободные порты) и
  post-checks (healthcheck контейнера).

### MediaMTX-специфичные переменные

| Переменная                 | По умолчанию | Описание                        |
|----------------------------|--------------|---------------------------------|
| `mediamtx_rtsp_port`       | `8554`       | Порт RTSP                       |
| `mediamtx_rtmp_port`       | `1935`       | Порт RTMP (отключен)            |
| `mediamtx_hls_port`        | `8888`       | Порт HLS (отключен)             |
| `mediamtx_webrtc_port`     | `8889`       | Порт WebRTC (отключен)          |
| `mediamtx_srt_port`        | `8890`       | Порт SRT (отключен)             |
| `mediamtx_ice_port`        | `8189`       | Порт WebRTC ICE (отключен)      |
| `mediamtx_rtsp_transports` | `tcp`        | Транспорт RTSP                  |
| `mediamtx_max_age_days`    | `7`          | Макс. возраст записей (дней)    |
| `mediamtx_max_size_gb`     | `50`         | Макс. размер архива (ГБ)        |
| `mediamtx_paths`           | `cam1`       | Список путей/камер              |

### Устранение неполадок

**«Authentication or permission failure»**

- Проверьте пароль:
  ```bash
  ansible -i ansible/inventory/hosts.yml tacitus -m ping \
    -e target_host=1.2.3.4 \
    -e deploy_user=root \
    -e deploy_password=your_password
  ```
- Убедитесь, что пользователь есть в `sudoers` без пароля: `your_user ALL=(ALL) NOPASSWD:ALL`
- Или добавьте `--ask-become-pass`

**«Could not resolve host» при получении сертификата**

- DNS A-запись должна быть настроена до запуска плейбука
- Проверьте: `dig +short example.com`

**«urn:ietf:params:acme:error:rateLimited»**

- Превышен лимит Let's Encrypt. Переключитесь на staging: `-e ssl_certbot_staging=true`

**Приложение не стартует после деплоя**

- Проверьте логи на сервере:
  ```bash
  ssh root@1.2.3.4 "cd /opt/tacitus && docker compose logs -f app"
  ```
- Убедитесь, что JWT_SECRET ≥ 8 символов и все обязательные переменные заданы

**MediaMTX не проходит healthcheck**

- Проверьте, что порт 8554 не занят другим процессом:
  ```bash
  ssh root@1.2.3.4 "ss -tlnp | grep 8554"
  ```
- Проверьте логи контейнера:
  ```bash
  ssh root@1.2.3.4 "cd /opt/tacitus && docker compose logs -f mediamtx"
  ```

---

## Переменные окружения (production)

Все dev defaults в `app/core/config.py` должны быть переопределены:

```bash
TACITUS_JWT_SECRET=<strong-random-secret>
TACITUS_S3_ACCESS_KEY=<minio-access-key>
TACITUS_S3_SECRET_KEY=<minio-secret-key>
```

- Секреты хранить только в утверждённом secret manager (например переменные окружения хоста)
- Никогда не коммитить `.env` (уже в `.gitignore`)
- Не логировать секреты
