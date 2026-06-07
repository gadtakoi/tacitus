# Инфраструктура

## Docker Compose

```yaml
services:
  app:              # FastAPI (Uvicorn)
  nginx:            # Reverse proxy + static files
  minio:            # S3-compatible storage
  minio-init:       # Bucket provisioning (init container)
  mediamtx:         # RTSP сервер и запись потоков
  mediamtx-cleanup: # Ротация записей и выгрузка старых сегментов в S3
```

## Nginx

- Порт `80` → внешний (HTTP)
- Порт `443` → внешний (HTTPS, опционально, через Let's Encrypt)
- `/api/` → proxy to `app:8000/` (FastAPI routers with `/api` prefix stripped)
- `/auth/` → proxy to `app:8000/auth/`
- `/files/` → proxy to `app:8000/files/`
- `/health` → proxy to `app:8000/health`
- `/streams/` → proxy to `app:8000/streams/`
- `/hls/` → proxy to `mediamtx:8888` (требует `hls: yes` в `mediamtx.yml`)
- `/webrtc/` → proxy to `mediamtx:8889` (требует `webrtc: yes` в `mediamtx.yml`)
- `/` → static React SPA (`/usr/share/nginx/html`)

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
- **Порты:** наружу экспонируется только **RTSP `8554/TCP`**
- **Протоколы:** включён только RTSP (`rtsp: yes`), ретрансляция отключена (`rtmp/hls/webrtc/srt: no`)
- **Запись:** `record: yes`, формат `fmp4`, сегментация по 1 часу
- **Volume:** `./recordings:/recordings` (bind mount на хосте)
- **Конфиг:** `./mediamtx/mediamtx.yml:/mediamtx.yml:ro`
- **Healthcheck:** `nc -z localhost 8554` (проверка доступности RTSP-порта)

## MediaMTX Cleanup

- **Образ:** `python:3.14-slim`
- **Назначение:** ротация архивов записей и выгрузка старых сегментов в S3/MinIO
- **Скрипт:** `scripts/mediamtx-archive-manager.py`
- **Частота:** раз в час (`sleep 3600`)
- **Логика:**
  - удаляет файлы старше `MAX_AGE_DAYS` дней с предварительной выгрузкой в S3
  - если суммарный размер превышает `MAX_SIZE_GB` — удаляет самые старые сегменты до снижения лимита
- **Volume:** `./recordings:/recordings` (общая директория с `mediamtx`)

## Security

- Версии образов зафиксированы (no `latest`)
- MinIO console недоступен извне
- Dev credentials только для локальной разработки
- MediaMTX не открывает порты HLS/WebRTC/RTMP/SRT наружу
- Записи хранятся в bind-mount `./recordings` с правами non-root пользователя
