# Инфраструктура

## Docker Compose

```yaml
services:
  app:       # FastAPI (Uvicorn)
  nginx:     # Reverse proxy + static files
  minio:     # S3-compatible storage
  minio-init:# Bucket provisioning (init container)
```

## Nginx

- Порт `80` → внешний (HTTP)
- Порт `443` → внешний (HTTPS, опционально, через Let's Encrypt)
- `/api/` → proxy to `app:8000/` (FastAPI routers with `/api` prefix stripped)
- `/auth/` → proxy to `app:8000/auth/`
- `/files/` → proxy to `app:8000/files/`
- `/health` → proxy to `app:8000/health`
- `/` → static React SPA (`/usr/share/nginx/html`)

### HTTPS (production)

SSL-сертификаты выпускаются через **Certbot** (Let's Encrypt). Подробнее см. `docs/deployment.md` → раздел «SSL / Let's
Encrypt».

## MinIO

- Внутренний порт `9000` (S3 API)
- Console `9001` (не exposed наружу)
- Bucket `tacitus-files` создаётся init-контейнером

## Security

- Версии образов зафиксированы (no `latest`)
- MinIO console недоступен извне
- Dev credentials только для локальной разработки
