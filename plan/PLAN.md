# План: SSL-сертификат и статистика S3

## 1. Получение бесплатного SSL-сертификата через Ansible + настраиваемый домен

### 1.1. Подготовка инфраструктуры

- [x] Добавить переменную `nginx_domain` в `ansible/group_vars/all.yml`
- [x] Добавить переменную `nginx_ssl_enabled` (default: false)
- [x] Добавить переменную `certbot_admin_email` для регистрации в Let's Encrypt
- [x] Добавить порт `443` в `docker-compose.yml` для сервиса `nginx`
- [ ] Проверить, что DNS A-запись домена указывает на целевой сервер

### 1.2. Ansible роль `ssl_cert`

- [x] Создать структуру роли:
  ```
  ansible/roles/ssl_cert/
  ├── tasks/
  │   └── main.yml
  └── defaults/
      └── main.yml
  ```
- [x] В `defaults/main.yml` задать:
  - `certbot_staging: true` (staging по умолчанию)
  - `certbot_agree_tos: true`
  - `certbot_auto_renew: true`
- [x] В `tasks/main.yml`:
  - [x] Установить `certbot` (standalone)
  - [x] Выполнить `certbot certonly` с `--standalone`
  - [x] Убедиться, что сертификат создан (`fullchain.pem` + `privkey.pem`)
- [ ] В `handlers/main.yml`:
  - [ ] Handler для перезагрузки Nginx после обновления сертификата

### 1.3. Настройка Nginx для HTTPS

- [x] Создать шаблон `ansible/roles/nginx_ssl/templates/nginx.conf.j2`
- [x] В шаблоне добавить:
  - [x] `listen 443 ssl http2;`
  - [x] `server_name {{ nginx_domain }};`
  - [x] `ssl_certificate .../fullchain.pem;`
  - [x] `ssl_certificate_key .../privkey.pem;`
  - [x] `ssl_protocols TLSv1.2 TLSv1.3;`
  - [x] `ssl_ciphers HIGH:!aNULL:!MD5;`
  - [x] `ssl_prefer_server_ciphers on;`
- [x] Добавить `location /.well-known/acme-challenge/` для webroot валидации certbot
- [x] Добавить редирект с HTTP (80) на HTTPS (443)
- [x] Обновить `nginx/nginx.conf` в проекте (dev-версия без SSL) — без изменений
- [x] Обновить `nginx/Dockerfile` — добавлен `EXPOSE 443`, volume для сертификатов в `docker-compose.yml`

### 1.4. Автообновление сертификата

- [x] Добавить cron для `certbot renew --quiet --deploy-hook "docker compose exec nginx nginx -s reload"`
- [ ] Проверить команду `certbot renew --dry-run`
- [ ] Убедиться, что обновление не требует остановки Nginx (standalone mode — требует остановки, для production рекомендуется webroot)

### 1.5. Интеграция роли в playbook

- [x] Добавить `ssl_cert` и `nginx_ssl` в `ansible/playbook.yml`
- [x] Условие выполнения ролей: `when: nginx_ssl_enabled | bool`
- [ ] Обновить `post_tasks` в playbook:
  - [ ] Проверка `https://{{ nginx_domain }}/health` вместо HTTP
  - [ ] Проверка редиректа с HTTP на HTTPS (301)

### 1.6. Тестирование

- [ ] Прогон Ansible с `certbot_staging: true` (требуется Linux target host)
- [ ] Проверка `curl -I https://{{ nginx_domain }}/` (200 OK + TLS)
- [ ] Проверка `curl -I http://{{ nginx_domain }}/` (301 Redirect)
- [ ] Проверка валидности сертификата через SSL Labs или `openssl s_client`
- [ ] Проверка автообновления (simulate via `--dry-run`)

---

## 2. Отображение статистики загруженных данных в S3 бакеты

### 2.1. Backend: сервисный слой

- [x] Создать Pydantic-схему `S3StatsResponse` в `app/schemas/stats.py`:
  - [x] `total_files: int`
  - [x] `total_size_bytes: int`
  - [x] `total_size_human: str`
  - [x] `largest_file: FileStat | None`
  - [x] `average_size_bytes: float`
- [x] Добавить метод `get_bucket_stats()` в `app/services/s3_service.py`:
  - [x] Вызвать `list_objects_v2` для получения списка объектов
  - [x] Для каждого объекта получить `Size` из `Contents`
  - [x] Подсчитать количество, суммарный размер, средний размер, максимальный размер
  - [x] Обработать пустой bucket (0 файлов)
  - [x] Обработать `ClientError` (нет доступа к bucket)
- [ ] Добавить кэширование на 60 секунд (опционально)

### 2.2. Backend: endpoint

- [x] Создать (или расширить) router в `app/api/routers/files.py`:
  - [x] `GET /files/stats` — возвращает `S3StatsResponse`
  - [x] Защитить endpoint через `Depends(get_current_user)`
  - [x] Добавить логирование (`request_id`, `user`)
- [x] Добавить тест `test_get_stats` в `tests/test_files_router.py`:
  - [x] Mock `s3_service.get_bucket_stats()`
  - [x] Проверка структуры ответа (все поля присутствуют)
  - [x] Проверка пустого bucket (все нули)

### 2.3. Backend: unit-тесты для S3Service

- [x] Добавить `test_get_bucket_stats` в `tests/test_s3_service.py`:
  - [x] Mock `list_objects_v2` с 3 объектами разного размера
  - [x] Проверка `total_files == 3`
  - [x] Проверка `total_size_bytes == сумма размеров`
  - [x] Проверка `largest_file` (имя и размер)
  - [x] Проверка пустого bucket (пустой `Contents`)

### 2.4. Frontend: API клиент

- [x] Добавить интерфейс `StatsResponse` и `FileStat` в `frontend/src/api/client.ts`
- [x] Добавить метод `filesApi.stats()`:
  - [x] `GET /api/files/stats` с `Authorization: Bearer <token>`

### 2.5. Frontend: компонент статистики

- [x] Добавить отображение статистики в `FileManager.tsx`:
  - [x] Отображение количества файлов
  - [x] Отображение общего размера (человекочитаемый формат)
  - [x] Отображение самого большого файла (имя + размер)
  - [x] Загрузка данных при монтировании компонента (`useEffect`)
  - [x] Обновление после upload / delete
- [x] Стилизация панели (inline styles)

### 2.6. Frontend: интеграция

- [x] Добавить stats-панель на страницу file manager (над списком файлов)
- [x] Обновление статистики после upload / delete
- [x] Проверка корректного отображения при пустом bucket (скрыта, stats == null)

### 2.7. Интеграционное тестирование

- [x] `pytest tests/` — 29 passed (включая stats endpoint и service)
- [x] Docker образ nginx собирается без ошибок (TypeScript компиляция пройдена)
- [ ] Ручная проверка: загрузить файлы, проверить что stats отражает изменения

---

## 3. Финальная верификация

### 3.1. SSL

- [x] `pytest tests/` все passed (29 passed)
- [x] `ruff check .` — passed
- [ ] Ansible playbook валиден (`ansible-playbook --syntax-check`) — требуется Linux host
- [ ] HTTPS endpoint доступен и валиден

### 3.2. Статистика

- [x] `GET /files/stats` возвращает корректный JSON
- [x] Frontend отображает статистику без ошибок
- [x] Логи не содержат секретов
