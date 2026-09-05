# Инфраструктура

## Docker Compose

```yaml
services:
  app:              # FastAPI (Uvicorn)
  nginx:            # Reverse proxy + static files
  postgres:         # PostgreSQL 18 — пользователи, токены, security-события
  db-migrations:    # Alembic upgrade head (init container, Exit 0)
  redis:            # Хранилище счётчиков rate limiting
  minio:            # S3-compatible storage
  minio-init:       # Bucket provisioning (init container, Exit 0)
  mediamtx:         # RTSP сервер и запись потоков
  mediamtx-cleanup: # Ротация записей и выгрузка старых сегментов в S3
```

Порядок запуска задан через `depends_on` с условиями: `app` стартует только
после `service_healthy` у `postgres` и `minio` и `service_completed_successfully`
у `minio-init` и `db-migrations`.

## PostgreSQL

- Образ `postgres:18.6-alpine` (точный патч-тег, см. раздел Security)
- Креды из `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB`
- Наружу пробрасывается `${POSTGRES_HOST_PORT:-15432}` (нестандартный порт,
  чтобы не конфликтовать с локальным PostgreSQL), **только на loopback** —
  `127.0.0.1` и `[::1]`. Порт нужен тестам с хоста; публикация на все
  интерфейсы выставляла бы БД в сеть. Переопределяется через
  `TACITUS_POSTGRES_BIND` вместе с правилом firewall.

  > Публикация на обоих loopback-адресах не избыточна: на Windows `localhost`
  > резолвится сначала в `::1`, и при публикации только на `127.0.0.1` каждое
  > подключение ждёт ~2 с таймаута IPv6 перед откатом на IPv4. Тестовые
  > фикстуры используют `NullPool` — соединение на тест, — поэтому прогон
  > замедлялся почти в десять раз.
- Данные в named volume `postgres-data`
- Healthcheck: `pg_isready`

## Redis

- Образ `redis:7.4.10-alpine`
- Используется как backend `TACITUS_RATE_LIMIT_STORAGE_URI` в production
- Данные в named volume `redis-data`
- Наружу не публикуется

## Nginx

- Порт `80` → внешний (HTTP)
- Порт `443` → внешний (HTTPS, опционально, через Let's Encrypt)
- `/api/` → proxy to `app:8000/` (префикс `/api` срезается, поэтому
  `/api/files/…` попадает в приложение как `/files/…`)
- `/auth/` → proxy to `app:8000/auth/`
- `/health` → proxy to `app:8000/health`
- `/.env` → `deny all` + 404
- `/.well-known/acme-challenge/` → webroot Certbot
- `/` → static React SPA (`/usr/share/nginx/html`)

> Локаций `/hls/` и `/webrtc/` **нет**. Они существовали, но проксировали в
> протоколы, выключенные в `mediamtx.yml` (`hls: no`, `webrtc: no`) в обеих
> конфигурациях — dev и production, — то есть были заведомо нерабочими. При
> этом каждая несла `Access-Control-Allow-Origin *` и собственную копию всех
> security-заголовков (nginx не наследует `add_header` в блок, который
> объявляет свои). Удалены: комплект заголовков теперь в одном месте, а
> production-шаблон Ansible этих локаций и не содержал — дрейф между двумя
> конфигами устранён. Включите HLS/WebRTC в MediaMTX — верните и локации.

> Отдельных location для `/files/` и `/streams/` **нет**: фронтенд ходит к ним
> через `/api/`. Прямой запрос на `http://host/files/...` уйдёт в SPA-fallback.

- Сервис `app` не публикует порт `8000` — приложение достижимо только через
  nginx. На этом основана модель доверенного прокси для `X-Forwarded-For`
  (см. `docs/security.md` → §2.7).

### HTTPS (production)

SSL-сертификаты выпускаются через **Certbot** (Let's Encrypt). Подробнее см. `docs/deployment.md` → раздел «SSL / Let's
Encrypt».

## MinIO

- Внутренний порт `9000` (S3 API)
- Console `9001` (не exposed наружу)
- Bucket `tacitus-files` создаётся init-контейнером

## MediaMTX

- **Образ:** `bluenviron/mediamtx:1.15.1-ffmpeg` (фиксированная версия, не `latest`)
- **Пользователь:** запуск от `1000:1000` (non-root)
- **Порты:** наружу экспонируется только **RTSP `8554/TCP`**, по умолчанию
  на loopback (`TACITUS_MTX_RTSP_BIND=127.0.0.1`)
- **Протоколы:** включён только RTSP (`rtsp: yes`), ретрансляция отключена (`rtmp/hls/webrtc/srt: no`)
- **Аутентификация:** `authInternalUsers` с разделением по действиям —
  `publisher` (publish), `reader` (read/playback), `tacitus-api` (api).
  См. `docs/security.md` §2.8 и ADR-012
- **Запись:** `record: yes`, формат `fmp4`, сегментация по 1 часу
- **Volume:** `./recordings:/recordings` (bind mount на хосте)
- **Конфиг:** `./mediamtx/mediamtx.yml:/mediamtx.yml:ro`, в production
  рендерится Ansible из `mediamtx.yml.j2`
- **Healthcheck:** `nc -z localhost 8554` (проверка доступности RTSP-порта)

## MediaMTX Cleanup

- **Образ:** `tacitus-app:0.1.0` — тот же, что у приложения
- **Назначение:** ротация архивов записей и выгрузка старых сегментов в S3/MinIO
- **Скрипт:** `scripts/mediamtx-archive-manager.py`
- **Зависимости:** `boto3` приходит из сборки образа. Установки пакетов при
  старте контейнера нет: сервис не зависит от доступности PyPI и использует
  тот же SDK, что и приложение
- **Пользователь:** non-root (`tacitus`, uid 1000) — как в образе приложения.
  Каталог записей должен принадлежать `1000:1000`, иначе ротация не сможет
  удалять локальные файлы (ansible создаёт его именно так)
- **Healthcheck:** отключён. Образ приложения объявляет HTTP-проверку порта
  8000, а здесь работает цикл ротации, а не uvicorn
- **Частота:** раз в час (`sleep 3600`)
- **Логика:**
  - удаляет файлы старше `MAX_AGE_DAYS` дней с предварительной выгрузкой в S3
  - если суммарный размер превышает `MAX_SIZE_GB` — удаляет самые старые сегменты до снижения лимита
- **Volume:** `./recordings:/recordings` (общая директория с `mediamtx`)

## Security

- Версии образов зафиксированы точными патч-тегами, а не только мажорными:
  `postgres:18.6-alpine`, `redis:7.4.10-alpine`, `python:3.14.5-slim`,
  `node:22.23.2-alpine`, `nginx:1.27.5-alpine`, MinIO и MediaMTX — по дате
  релиза. Мажорный тег вроде `18-alpine` не `latest`, но всё равно движется:
  пересборка через полгода принесла бы другой патч-релиз без единого
  изменения в репозитории. Обновление версии — осознанная правка строки;
  проверяется тестом `test_upstream_images_are_pinned_to_an_exact_release`
- MinIO console недоступен извне
- Dev credentials только для локальной разработки
- MediaMTX не открывает порты HLS/WebRTC/RTMP/SRT наружу
- MediaMTX требует аутентификации для publish, read и Control API; анонимный
  дефолт вытеснен явным `authInternalUsers`
- RTSP-порт по умолчанию слушает только loopback
- Порт PostgreSQL публикуется только на loopback (`127.0.0.1` и `[::1]`)
- Записи хранятся в bind-mount `./recordings` с правами non-root пользователя
