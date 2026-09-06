# Деплой

## Локальный запуск

```bash
docker compose up -d
```

### Настройка PostgreSQL

В Docker Compose сервис `postgres` поднимается автоматически. При первом
запуске он создаёт базу, пользователя и пароль из переменных окружения
`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`.

Для локальной разработки без Docker убедитесь, что PostgreSQL доступен и
создана база данных:

```bash
# Пример для psql
createdb -U tacitus tacitus
```

Установите `TACITUS_DATABASE_URL`:

```bash
export TACITUS_DATABASE_URL="postgresql+asyncpg://tacitus:tacitus@localhost:5432/tacitus"
```

### Rate limiting backend

По умолчанию используется `memory://` (подходит только для одного процесса).
Для production или нескольких инстансов приложения настройте Redis:

```bash
export TACITUS_RATE_LIMIT_STORAGE_URI="redis://redis:6379"
```

### Применение миграций базы данных

В Docker Compose миграции применяются автоматически сервисом `db-migrations`
при каждом запуске стека:

```bash
docker compose up -d db-migrations
```

При локальной разработке без Docker примените миграции вручную:

```bash
# Убедитесь, что переменная TACITUS_DATABASE_URL задана
export TACITUS_DATABASE_URL="postgresql+asyncpg://tacitus:tacitus@localhost:5432/tacitus"

# Применить все миграции
alembic upgrade head

# Откатить последнюю миграцию
alembic downgrade -1

# Перегенерировать миграцию после изменения моделей
alembic revision --autogenerate -m "describe your change"
```

> **Важно:** никогда не редактируйте существующие миграции после их применения
> в production. Если модель изменилась — создайте новую миграцию.

---

## Инициализация проекта: первый пользователь

Миграции создают схему, но не заводят учётные записи. Пока в базе нет ни одного
пользователя, войти в приложение нельзя: `POST /auth/login` отвечать некому.
Этот шаг закрывает `scripts/init.sh`.

```bash
# Интерактивно: спросит логин и пароль (ввод пароля не отображается)
scripts/init.sh

# С генерацией пароля — он будет напечатан один раз
scripts/init.sh --username admin --generate
```

Стек при этом должен быть запущен (`docker compose up -d`): скрипт выполняет
`docker compose exec app python scripts/init_project.py`, то есть работает
внутри контейнера приложения, где `TACITUS_DATABASE_URL` уже указывает на
PostgreSQL. Файл `scripts/init_project.py` попадает в образ на сборке; если
образ старый, скрипт скажет пересобрать его (`docker compose up -d --build app`).

**Свойства скрипта:**

- **Идемпотентность.** Повторный запуск с тем же логином ничего не меняет,
  пароль существующей учётной записи не перезаписывается, код выхода `0`.
  Поэтому вызов безопасно ставить после каждого деплоя.
- **Пароль не передаётся аргументом.** `argv` виден в выводе `ps` любому
  пользователю хоста и сохраняется в истории оболочки. Источники пароля, в
  порядке приоритета: `--password-stdin`, переменная `TACITUS_INIT_PASSWORD`,
  `--generate`, интерактивный запрос.
- **Единые правила с API.** Логин и пароль проверяются схемой
  `RegisterRequest` — той же, что и в `POST /auth/register` (см.
  [Password Policy](security.md#23-password-policy)), а сама учётная запись
  создаётся сервисом `register_user`. Хеш пароля получается тем же
  `pbkdf2_sha256`, что и при регистрации через API.
- **Аудит.** Создание пишется в security-лог событием `auth.register` без поля
  `ip` — HTTP-клиента, которому его можно приписать, здесь нет.

Автоматизация (CI, плейбук) — пароль по конвейеру, без TTY:

```bash
printf '%s' "$TACITUS_ADMIN_PASSWORD" | scripts/init.sh --username admin --password-stdin
```

Без Docker (локальная разработка, зависимости проекта установлены):

```bash
export TACITUS_DATABASE_URL="postgresql+asyncpg://tacitus:tacitus@localhost:15432/tacitus"
scripts/init.sh --local --username admin --generate
```

Коды выхода: `0` — пользователь создан или уже существовал; `1` — база
недоступна либо не применены миграции; `2` — неверные аргументы или пароль не
проходит политику. Полная справка — `scripts/init.sh --help`.

---

## Быстрый деплой одним скриптом

```bash
scripts/deploy.sh 1.2.3.4
```

Это рекомендуемый способ. Скрипт разворачивает проект целиком за один запуск и
не требует Ansible на вашей машине — достаточно `git`, `ssh` и `curl`. Работает
из Git Bash на Windows, где Ansible не запускается нативно.

Как это устроено: `git archive HEAD` уезжает на сервер одним SSH-соединением,
там ставится Ansible, и `ansible/playbook.yml` выполняется локально
(`connection_type=local`). Поэтому сети между вашей машиной и Ansible не нужно —
хватает работающего SSH.

С доменом и HTTPS:

```bash
scripts/deploy.sh 1.2.3.4 --domain example.com --email admin@example.com --https --staging
```

Обновить только код, без переустановки Docker:

```bash
scripts/deploy.sh 1.2.3.4 --tags app
```

Обновить пакеты ОС перед деплоем (см. [Обновление пакетов ОС](#обновление-пакетов-ос)):

```bash
scripts/deploy.sh 1.2.3.4 --update-os
```

Полный список опций — `scripts/deploy.sh --help`.

### Что скрипт делает на сервере

- ставит `ansible`, `rsync` и `sshpass`, если их нет;
- создаёт swap на 2 ГБ, если RAM меньше 2 ГиБ и swap отсутствует (без него
  сборка фронтенда в Docker падает по OOM); отключается флагом `--no-swap`;
- генерирует секреты в `/etc/tacitus/deploy-secrets.yml` (режим 600) при первом
  запуске и **переиспользует** их дальше;
- запускает плейбук и проверяет `/health` снаружи, с вашей машины.

> **Про секреты.** Они создаются на сервере и не проходят через историю shell на
> управляющей машине. Переиспользование обязательно: PostgreSQL применяет пароль
> только при инициализации тома, поэтому новый пароль на повторном деплое
> оставил бы приложение с credentials, которые база уже не принимает. Чтобы
> задать свои значения, отредактируйте `/etc/tacitus/deploy-secrets.yml` до
> первого деплоя или передайте их через `-e`.

### Требования

- **Локально:** `git`, `ssh`, `curl`.
- **На сервере:** Debian/Ubuntu, вход под `root` либо пользователь с беспарольным
  `sudo`. SSH-ключ (`ssh-copy-id`) избавит от ввода пароля; иначе `ssh` спросит
  его один раз.

Деплоится **закоммиченное состояние** (`HEAD`), а не рабочая копия — так на
сервере оказывается ровно то, что воспроизводимо по коммиту. Если в рабочей
копии есть незакоммиченные правки, скрипт предупредит об этом.

---

## Развёртывание через Ansible

Ручной путь — когда нужен контроль над отдельными шагами. Обычный деплой проще
делать через `scripts/deploy.sh` (см. выше).

### Option 1: WSL2 (Recommended)

```bash
# Install WSL2
wsl --install -d Ubuntu
wsl -d Ubuntu
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
uv pip install "ansible-core>=2.21.0"

# sshpass нужен для подключения по паролю (ansible_ssh_pass),
# rsync — для ansible.posix.synchronize (синхронизация frontend/)
sudo apt update && sudo apt install -y sshpass rsync

mkdir -p ~/.ssh/
ssh-keyscan -H 1.2.3.4 >> ~/.ssh/known_hosts

# Inside WSL — путь к репозиторию на диске C:
cd /mnt/c/path/to/tacitus
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbook.yml \
  -e target_host=1.2.3.4 \
  -e deploy_user=root \
  --ask-pass --ask-become-pass
```

> Не подставляйте реальные адреса и пароли в примеры команд: они попадают в
> историю shell и в репозиторий. Используйте `--ask-pass` или SSH-ключи.

> **Безопасность:** не храните пароль сервера в скриптах и истории shell.
> Предпочтительные варианты:
> `ansible-playbook ... --ask-pass` (интерактивный ввод пароля) или
> SSH-ключи (`ssh-copy-id root@<host>`).

Ansible-плейбук автоматически устанавливает Docker, копирует код проекта на сервер, создаёт файл окружения `.env` и
запускает приложение через `docker compose`. При необходимости получает SSL-сертификат Let's Encrypt и настраивает
HTTPS.

### Требования

**На управляющей машине (ваш ПК):**

- Python 3.10+
- Ansible (`ansible-core >= 2.21.0`, см. `pyproject.toml`)
- `sshpass` — если используется подключение по паролю (`ansible_ssh_pass`)
- `rsync` — для `ansible.posix.synchronize` (синхронизация `frontend/`)
- Коллекция `ansible.posix` (`ansible-galaxy collection list | grep posix`)

**На целевом сервере:**

- Ubuntu 20.04+ / Debian 11+ (роль `docker_install` заточена под `apt`)
- SSH-доступ с правами `sudo` (безпарольный `sudo` или пароль через `--ask-become-pass`)
- `rsync` установлен (обычно есть в Ubuntu; в минимальных образах может отсутствовать)
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

> ⚠️ Команды ниже показывают только параметры подключения. Плейбук деплоит в
> режиме `production`, поэтому к каждой из них нужно добавить секреты из раздела
> [«Передача секретов»](#передача-секретов) — иначе pre-check остановит деплой.

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

Плейбук деплоит с `tacitus_environment=production`, поэтому **секреты обязаны быть
переопределены** — иначе pre-check остановит деплой до копирования файлов:

```bash
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbook.yml \
  -e target_host=1.2.3.4 \
  -e deploy_user=root \
  -e deploy_password=your_password \
  -e tacitus_jwt_secret="$(openssl rand -hex 32)" \
  -e tacitus_s3_access_key="$(openssl rand -hex 16)" \
  -e tacitus_s3_secret_key="$(openssl rand -hex 32)" \
  -e tacitus_postgres_password="$(openssl rand -hex 24)"
```

Pre-check `Refuse to deploy production with default secrets` проверяет, что
`tacitus_jwt_secret` не дефолтный и длиной ≥ 32 символов, что S3-ключи не
`minioadmin`, что пароль PostgreSQL переопределён и что в CORS-origins нет `*`.

Для быстрой проверки стенда без секретов можно явно снизить режим — тогда
startup-проверки приложения отключаются, и это **не** годится для боевого хоста:

```bash
ansible-playbook ... -e tacitus_environment=development
```

> **Про имена переменных.** `docker-compose.yml` интерполирует S3-ключи и
> параметры PostgreSQL по **безпрефиксным** именам (`S3_ACCESS_KEY`,
> `POSTGRES_PASSWORD`, …), потому что их делят между собой сервисы `app`,
> `minio` и `mediamtx-cleanup`. Приложение при этом читает `TACITUS_S3_*`.
> Значения `environment:` в compose приоритетнее `env_file:`, поэтому строка
> `TACITUS_S3_ACCESS_KEY=...` в `.env` будет **молча проигнорирована**.
> Шаблон `.env.j2` пишет обе группы имён в правильном виде — не «чините» его,
> добавляя префикс.

> ⚠️ **Никогда не коммитьте пароли в репозиторий.** Передавайте секреты через `-e` прямо в командной строке (для разовых
> деплоев) или через переменные окружения управляющей машины:
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
| `tacitus_jwt_secret`    | `change-me-in-production` | JWT-секрет приложения (≥ 32 символов) |
| `tacitus_s3_access_key` | `minioadmin`              | S3 access key                         |
| `tacitus_s3_secret_key` | `minioadmin`              | S3 secret key                         |
| `tacitus_deploy_dir`    | `/opt/tacitus`            | Путь установки на сервере             |
| `tacitus_environment`   | `production`              | Режим приложения; `production` включает startup-проверки |
| `tacitus_cors_origins`  | домен или IP хоста        | Разрешённые CORS-origins (в production нельзя `*`) |
| `tacitus_trusted_proxy_hops` | `1`                  | Число обратных прокси перед приложением |
| `tacitus_s3_endpoint`   | `http://minio:9000`       | Endpoint S3/MinIO                     |
| `tacitus_s3_bucket`     | `tacitus-files`           | Bucket для файлов                     |
| `tacitus_postgres_user` | `tacitus`                 | Пользователь PostgreSQL               |
| `tacitus_postgres_password` | `change-me-in-production` | Пароль PostgreSQL                 |
| `tacitus_postgres_db`   | `tacitus`                 | Имя базы данных                       |
| `tacitus_rate_limit_storage_uri` | `redis://redis:6379` | Backend для rate limiting          |
| `firewall_enabled`     | `true`                    | Настраивать `ufw` на хосте (только Debian/Ubuntu) |
| `unattended_upgrades_enabled` | `true`              | Включать автообновления безопасности (только Debian/Ubuntu) |

Полный список — в файлах `ansible/group_vars/all.yml` и `ansible/roles/*/defaults/main.yml`.

### Резервное копирование базы данных

Рекомендуется регулярно резервировать PostgreSQL. Примеры команд:

```bash
# Логический бэкап через pg_dump
pg_dump -U tacitus -h localhost -Fc tacitus > tacitus_$(date +%Y%m%d).dump

# Восстановление из бэкапа
pg_restore -U tacitus -h localhost -d tacitus tacitus_20260101.dump
```

В Docker Compose можно выполнить бэкап из контейнера:

```bash
docker compose exec postgres pg_dump -U tacitus -Fc tacitus > tacitus_backup.dump
```

Рекомендации:

- Настройте ежедневные бэкапы через `cron` или инструмент оркестрации.
- Храните бэкапы вне сервера приложения (S3, отдельный диск).
- Проверяйте восстановление из бэкапа не реже раза в месяц.

### Выборочный запуск (теги)

| Тег                  | Что делает                                          |
|----------------------|------------------------------------------------------|
| `os-update`          | Только обновление пакетов ОС (нужен `os_update_enabled=true`) |
| `packages`           | Синоним `os-update`                                  |
| `docker`             | Только установка Docker                              |
| `firewall`           | Только настройка `ufw`                               |
| `unattended-upgrades`| Только настройка автообновлений безопасности         |
| `security`           | `firewall` + `unattended-upgrades` вместе            |
| `ssl`                | Только получение/обновление SSL                      |
| `app`                | Только копирование кода и перезапуск контейнеров     |
| `mediamtx`           | Только деплой MediaMTX конфигурации и запуск         |

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

### Обновление пакетов ОС

Роль `os_update` обновляет пакеты самой операционной системы. Она **опциональна и выключена по умолчанию**:
обновление пакетов меняет состояние хоста способом, который деплой приложения не откатывает — перезапускаются
системные сервисы, может потребоваться перезагрузка. Поэтому шаг включается осознанно, а не приезжает вместе с
обычным деплоем кода.

Регулярные security-патчи ставит роль `unattended_upgrades` — она включена по умолчанию, и для неё этот флаг не
нужен. `os_update` нужна для другого: разово подтянуть **все** обновления перед выкаткой или после долгого простоя
сервера.

Через скрипт деплоя:

```bash
# apt-get upgrade: не удаляет установленные пакеты
scripts/deploy.sh 1.2.3.4 --update-os

# apt-get dist-upgrade: разрешает удаление пакетов ради зависимостей (новое ядро)
scripts/deploy.sh 1.2.3.4 --update-os-full
```

Напрямую через Ansible:

```bash
# Обновить пакеты и развернуть приложение
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbook.yml \
  -e target_host=1.2.3.4 \
  -e deploy_user=root \
  -e os_update_enabled=true

# Только обновить пакеты, ничего не деплоя
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbook.yml \
  -e target_host=1.2.3.4 \
  -e deploy_user=root \
  -e os_update_enabled=true \
  --tags os-update
```

| Переменная                   | По умолчанию | Описание                                                        |
|------------------------------|--------------|-----------------------------------------------------------------|
| `os_update_enabled`          | `false`      | Включает роль. Без него роль пропускается целиком                |
| `os_update_upgrade`          | `safe`       | `safe` — `apt-get upgrade`; `full`/`dist` — `dist-upgrade`       |
| `os_update_cache_valid_time` | `3600`       | Не перечитывать индекс apt, если он свежее N секунд              |
| `os_update_autoremove`       | `true`       | `apt-get autoremove` после обновления                            |
| `os_update_autoclean`        | `true`       | `apt-get autoclean` — чистит кеш `.deb`                          |
| `os_update_reboot`           | `false`      | Перезагрузить хост, если обновление этого требует                |
| `os_update_reboot_timeout`   | `600`        | Сколько секунд ждать возвращения хоста после перезагрузки        |

**Про перезагрузку.** По умолчанию роль не перезагружает сервер: на хосте живут PostgreSQL и, возможно, идущая
запись RTSP, и незапланированный ребут — худший исход, чем отложенное обновление ядра. Если перезагрузка нужна,
роль напишет об этом в выводе, а факт фиксируется файлом `/var/run/reboot-required` (его же проверяет
`scripts/healthcheck.sh`). Включить автоматический ребут: `-e os_update_reboot=true`.

Учтите: при деплое через `scripts/deploy.sh` Ansible работает **на самом сервере** с локальным подключением, и
перезагрузка убила бы процесс деплоя. В этом случае роль намеренно не перезагружает хост даже при
`os_update_reboot=true`, а сообщает, что перезагрузку нужно сделать вручную.

**Риски и порядок применения.** Обновление пакетов не откатывается плейбуком. Перед `--update-os` на боевом
сервере: снимите бэкап БД (см. [Резервное копирование](#резервное-копирование-базы-данных)), выполняйте в окно
обслуживания, после — прогоните `scripts/healthcheck.sh <host>`.

### Структура ролей

- **`os_update`** — обновляет пакеты самой ОС через `apt` (`update` → `upgrade` → `autoremove` → `autoclean`) и
  сообщает, если для завершения нужна перезагрузка. **Выключена по умолчанию**, включается явно
  (`-e os_update_enabled=true` или `scripts/deploy.sh --update-os`). Запускается **первой**: свежий индекс apt нужен
  до установки Docker, а перезагрузка (если её разрешили) случается до того, как на хосте что-то поднимется. Подробнее
  — [Обновление пакетов ОС](#обновление-пакетов-ос).
- **`docker_install`** — устанавливает Docker Engine и Docker Compose plugin (только Debian/Ubuntu), добавляет
  пользователя в группу `docker`.
- **`ssl_cert`** — устанавливает Certbot, получает SSL-сертификат через webroot-режим, деплоит nginx-конфиг с HTTPS,
  настраивает автообновление по cron.
- **`firewall`** — устанавливает `ufw`, включает default-deny на входящие соединения, открывает SSH-порт (тот же,
  которым Ansible уже подключился — переопределить не получится случайно) и `80`/`443`. Запускается **до**
  `ssl_cert`, чтобы webroot-проверке Let's Encrypt было куда стучаться, и **до** `app_deploy`, чтобы хост был закрыт
  прежде, чем на нём поднимется что-либо. Только Debian/Ubuntu; отключается `-e firewall_enabled=false`.
- **`unattended_upgrades`** — устанавливает `unattended-upgrades`, включает ежедневную установку security-патчей
  через `/etc/apt/apt.conf.d/20auto-upgrades`. Origins-паттерн (что считается «security») не переопределяется —
  используется тот, что уже задан пакетом дистрибутива. Автоматический перезапуск **выключен по умолчанию**
  (`unattended_upgrades_auto_reboot: false`): хост держит PostgreSQL и, возможно, идущую запись RTSP, и
  незапланированный ребут — худший исход, чем отложенное обновление ядра. Отключается `-e
  unattended_upgrades_enabled=false`.
- **`app_deploy`** — создаёт директорию `/opt/tacitus`, копирует исходный код, Dockerfile, docker-compose.yml, рендерит
  `.env` из шаблона, запускает `docker compose up --build -d`.
- **`mediamtx`** — создаёт директорию `./recordings` с владельцем `1000:1000`, рендерит `mediamtx.yml` из шаблона,
  запускает/перезапускает контейнер `mediamtx`, выполняет pre-checks (доступность Docker, свободные порты) и
  post-checks (healthcheck контейнера).

### MediaMTX-специфичные переменные

| Переменная                 | По умолчанию | Описание                     |
|----------------------------|--------------|------------------------------|
| `mediamtx_rtsp_port`       | `8554`       | Порт RTSP                    |
| `mediamtx_rtmp_port`       | `1935`       | Порт RTMP (отключен)         |
| `mediamtx_hls_port`        | `8888`       | Порт HLS (отключен)          |
| `mediamtx_webrtc_port`     | `8889`       | Порт WebRTC (отключен)       |
| `mediamtx_srt_port`        | `8890`       | Порт SRT (отключен)          |
| `mediamtx_ice_port`        | `8189`       | Порт WebRTC ICE (отключен)   |
| `mediamtx_rtsp_transports` | `tcp`        | Транспорт RTSP               |
| `mediamtx_max_age_days`    | `7`          | Макс. возраст записей (дней) |
| `mediamtx_max_size_gb`     | `50`         | Макс. размер архива (ГБ)     |
| `mediamtx_paths`           | `cam1`       | Список путей/камер           |

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
- Убедитесь, что `TACITUS_JWT_SECRET` ≥ **32** символов и все обязательные переменные заданы.
  При `TACITUS_ENVIRONMENT=production` приложение намеренно падает с `RuntimeError`,
  если секрет короче 32 символов, S3-ключи равны `minioadmin` или в CORS-origins есть `*`.

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

## Проверка состояния сервера

`scripts/healthcheck.sh` отвечает на вопрос «всё ли установилось и работает так, как задумано». Скрипт **только
читает** состояние: ничего не ставит, не перезапускает и не меняет конфигурацию, поэтому его безопасно запускать
на боевом сервере в любой момент.

```bash
# С рабочей машины: скрипт уезжает на сервер через stdin и выполняется там
scripts/healthcheck.sh 1.2.3.4

# Под другим пользователем и по ключу
scripts/healthcheck.sh 1.2.3.4 --user ubuntu --ssh-key ~/.ssh/id_ed25519

# На самом сервере
ssh root@1.2.3.4 "cd /opt/tacitus && bash scripts/healthcheck.sh"
```

Ничего копировать на сервер не нужно — скрипт самодостаточен и передаётся одним SSH-соединением. Как и
`deploy.sh`, работает из Git Bash на Windows.

### Что проверяется

| Блок                     | Проверки                                                                                      |
|--------------------------|-----------------------------------------------------------------------------------------------|
| Хост                     | Debian-семейство, свободное место, RAM/swap, ожидающая перезагрузка                            |
| Docker                   | бинарник, плагин `compose`, доступность демона, автозапуск                                     |
| Файлы деплоя             | наличие всего, что копируют роли; права `600` на `.env` и `/etc/tacitus/deploy-secrets.yml`     |
| Конфигурация             | `TACITUS_ENVIRONMENT=production`, выключенный debug, длина JWT-секрета, переопределены ли дефолтные секреты, отсутствие `*` в CORS |
| Контейнеры               | все сервисы `running` и `healthy`; `db-migrations` и `minio-init` завершились с кодом 0        |
| Приложение               | `/health` отдаёт `{"status":"ok"}`                                                             |
| HTTPS                    | срок действия сертификата, staging-CA, настроенное автообновление, редирект HTTP → HTTPS       |
| Сеть                     | `ufw` активен с default-deny, открыты 80/443 и SSH; PostgreSQL и RTSP не опубликованы наружу   |
| Автообновления           | `unattended-upgrades` установлен и включён, когда отрабатывал, сколько обновлений ждёт установки |
| Данные и логи            | тома PostgreSQL/MinIO/Redis, `/var/log/tacitus`, владелец `recordings/`                        |

Значения секретов **не выводятся**: скрипт сравнивает их с известными дефолтами и печатает только имя переменной
и вердикт. Вывод безопасно приложить к тикету.

### Коды выхода

| Код | Значение                                                                 |
|-----|--------------------------------------------------------------------------|
| `0` | Критичных проблем нет. Возможны `WARN` — их стоит прочитать               |
| `1` | Есть `FAIL`: сервер настроен не полностью. С `--strict` — и при `WARN`    |
| `2` | Проверку не удалось выполнить (нет `ssh`, неверные аргументы)             |

Про `--strict`: `WARN` — это то, что работает, но однажды сломается (сертификат на исходе, диск на 85%, порт БД
наружу). В мониторинге или CI имеет смысл считать их ошибками, при ручном запуске — читать глазами.

Часть проверок требует root (`.env` имеет режим `600`, `docker` и `ufw` обычно тоже). Скрипт сам использует
`sudo -n`, если он доступен без пароля, а иначе честно помечает пропущенные проверки.

### Когда запускать

- сразу после первого деплоя — убедиться, что всё встало;
- после `--update-os` и после перезагрузки сервера;
- при разборе инцидента — как первый шаг перед чтением логов;
- по расписанию из `cron` с `--strict`, если нужен простой сигнал «сервер в порядке».

---

## Переменные окружения (production)

Все dev defaults в `app/core/config.py` должны быть переопределены:

```bash
TACITUS_JWT_SECRET=<strong-random-secret>
TACITUS_DATABASE_URL=postgresql+asyncpg://tacitus:<password>@postgres:5432/tacitus
TACITUS_RATE_LIMIT_STORAGE_URI=redis://redis:6379
TACITUS_S3_ACCESS_KEY=<minio-access-key>
TACITUS_S3_SECRET_KEY=<minio-secret-key>
```

- Секреты хранить только в утверждённом secret manager (например переменные окружения хоста)
- Никогда не коммитить `.env` (уже в `.gitignore`)
- Не логировать секреты
- `TACITUS_RATE_LIMIT_STORAGE_URI` должен указывать на Redis в production,
  иначе лимиты не будут общими между инстансами
