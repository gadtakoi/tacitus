# Tacitus

![Coverage](https://img.shields.io/badge/coverage-90%25-green)

Tacitus — fullstack-приложение для управления файлами в S3-совместимом хранилище (MinIO) с авторизацией через JWT и
поддержкой RTSP-потоков (MediaMTX).

## Стек

- **Backend:** Python 3.14, FastAPI, Uvicorn, SQLAlchemy 2.0, asyncpg, Alembic
- **Frontend:** React 19, TypeScript, Vite
- **Database:** PostgreSQL 15+
- **Storage:** MinIO (S3-compatible)
- **Media:** MediaMTX (RTSP-сервер с записью потоков)
- **Proxy:** Nginx
- **Rate limiting:** slowapi + limits (`memory://` dev, `redis://` production)
- **Deployment:** Docker Compose, Ansible

## Быстрый старт

```bash
# 1. Клонировать репозиторий
git clone <repo-url>
cd tacitus

# 2. Создать виртуальное окружение
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# 3. Установить pre-commit hooks
pre-commit install

# 4. Запустить стек
docker compose up -d

# 5. Открыть http://localhost
```

## API

| Endpoint          | Method | Auth   | Описание             |
|-------------------|--------|--------|----------------------|
| `/`               | GET    | Нет    | Hello World          |
| `/health`         | GET    | Нет    | Health check         |
| `/auth/register`  | POST   | Нет    | Регистрация          |
| `/auth/login`     | POST   | Нет    | Логин (JWT)          |
| `/auth/refresh`   | POST   | Нет    | Обмен refresh-токена на новую пару (с ротацией) |
| `/auth/logout`    | POST   | Bearer | Отзыв access- и refresh-токена |
| `/auth/me`        | GET    | Bearer | Текущий пользователь |
| `/files/`         | GET    | Bearer | Список файлов        |
| `/files/upload`   | POST   | Bearer | Загрузка файла       |
| `/files/stats`    | GET    | Bearer | Статистика bucket    |
| `/files/{name}`   | GET    | Bearer | Presigned URL        |
| `/files/{name}`   | DELETE | Bearer | Удаление файла       |
| `/streams`        | GET    | Bearer | Список RTSP-потоков  |
| `/streams/{path}` | GET    | Bearer | Информация о потоке  |

## Тестирование

```bash
# Линтинг
ruff check .

# Тесты
pytest tests/ -v

# Покрытие
pytest tests/ --cov=app --cov-report=term-missing
```

## Документация

- [Архитектура backend](docs/backend.md)
- [Frontend](docs/frontend.md)
- [Инфраструктура](docs/infrastructure.md)
- [Тестирование](docs/testing.md)
- [Деплой](docs/deployment.md)

## Переменные окружения

Настройки приложения (`app/core/config.py`), префикс `TACITUS_`:

| Переменная                            | Default                                                      | Описание                                                 |
|---------------------------------------|--------------------------------------------------------------|----------------------------------------------------------|
| `TACITUS_ENVIRONMENT`                 | `development`                                                | `production` включает startup-проверки секретов          |
| `TACITUS_DEBUG`                       | `false`                                                      | Текст исключения в ответе 500 и SQL-echo                 |
| `TACITUS_JWT_SECRET`                  | `dev-jwt-secret-change-me`                                   | Секрет для JWT (в production ≥ 32 символов)              |
| `TACITUS_JWT_ALGORITHM`               | `HS256`                                                      | Алгоритм JWT                                             |
| `TACITUS_JWT_EXPIRATION_MINUTES`      | `15`                                                         | TTL access-токена                                        |
| `TACITUS_JWT_REFRESH_EXPIRATION_DAYS` | `7`                                                          | TTL refresh-токена                                       |
| `TACITUS_CORS_ORIGINS`                | `http://localhost:3000`                                      | Разрешённые origins через запятую (в production без `*`) |
| `TACITUS_DATABASE_URL`                | `postgresql+asyncpg://tacitus:tacitus@postgres:5432/tacitus` | URL PostgreSQL через asyncpg                             |
| `TACITUS_RATE_LIMIT_STORAGE_URI`      | `memory://`                                                  | Backend rate limiting (`memory://` dev, `redis://` prod) |
| `TACITUS_TRUSTED_PROXY_HOPS`          | `1`                                                          | Число обратных прокси перед приложением; `0` — прямой доступ |
| `TACITUS_S3_ENDPOINT`                 | `http://minio:9000`                                          | URL MinIO                                                |
| `TACITUS_S3_ACCESS_KEY`               | `minioadmin`                                                 | MinIO access key                                         |
| `TACITUS_S3_SECRET_KEY`               | `minioadmin`                                                 | MinIO secret key                                         |
| `TACITUS_S3_BUCKET`                   | `tacitus-files`                                              | Bucket для файлов                                        |
| `TACITUS_PRESIGNED_URL_EXPIRATION`    | `300`                                                        | TTL presigned URL, сек                                   |
| `TACITUS_MTX_API_URL`                 | `http://mediamtx:9997/v3`                                    | URL Control API MediaMTX                                 |

Переменные уровня `docker-compose.yml` — приложение их **не** читает, они
конфигурируют соседние сервисы:

| Переменная                 | Default         | Кто использует                       |
|----------------------------|-----------------|--------------------------------------|
| `S3_ACCESS_KEY`            | `minioadmin`    | `app`, `minio`, `mediamtx-cleanup`   |
| `S3_SECRET_KEY`            | `minioadmin`    | `app`, `minio`, `mediamtx-cleanup`   |
| `S3_ENDPOINT` / `S3_BUCKET`| см. выше        | `app`, `minio-init`, `mediamtx-cleanup` |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `tacitus` | `postgres`      |
| `TACITUS_MTX_RTSP_PORT`    | `8554`          | `mediamtx` (RTSP-порт и healthcheck) |
| `TACITUS_MTX_MAX_AGE_DAYS` | `7`             | `mediamtx-cleanup`                   |
| `TACITUS_MTX_MAX_SIZE_GB`  | `50`            | `mediamtx-cleanup`                   |

> ⚠️ Задавать S3-ключи как `TACITUS_S3_ACCESS_KEY` в `.env` **бесполезно**:
> блок `environment:` в compose перекрывает `env_file:` значением
> `${S3_ACCESS_KEY:-minioadmin}`. Используйте безпрефиксные имена.

> ⚠️ **В production** все dev defaults должны быть переопределены через env vars.

## Лицензия

MIT
