# Tacitus

![Coverage](https://img.shields.io/badge/coverage-92%25-green)

Tacitus — fullstack-приложение для управления файлами в S3-совместимом хранилище (MinIO) с авторизацией через JWT.

## Стек

- **Backend:** Python 3.14, FastAPI, Uvicorn
- **Frontend:** React 18, TypeScript, Vite
- **Storage:** MinIO (S3-compatible)
- **Proxy:** Nginx
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

| Endpoint         | Method | Auth   | Описание             |
|------------------|--------|--------|----------------------|
| `/`              | GET    | Нет    | Hello World          |
| `/health`        | GET    | Нет    | Health check         |
| `/auth/register` | POST   | Нет    | Регистрация          |
| `/auth/login`    | POST   | Нет    | Логин (JWT)          |
| `/auth/me`       | GET    | Bearer | Текущий пользователь |
| `/files/`        | GET    | Bearer | Список файлов        |
| `/files/upload`  | POST   | Bearer | Загрузка файла       |
| `/files/{name}`  | GET    | Bearer | Presigned URL        |
| `/files/{name}`  | DELETE | Bearer | Удаление файла       |

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

| Переменная                       | Default                   | Описание          |
|----------------------------------|---------------------------|-------------------|
| `TACITUS_JWT_SECRET`             | `change-me-in-production` | Секрет для JWT    |
| `TACITUS_JWT_ALGORITHM`          | `HS256`                   | Алгоритм JWT      |
| `TACITUS_JWT_EXPIRATION_MINUTES` | `30`                      | TTL токена        |
| `TACITUS_S3_ENDPOINT`            | `http://minio:9000`       | URL MinIO         |
| `TACITUS_S3_ACCESS_KEY`          | `minioadmin`              | MinIO access key  |
| `TACITUS_S3_SECRET_KEY`          | `minioadmin`              | MinIO secret key  |
| `TACITUS_S3_BUCKET`              | `tacitus-files`           | Bucket для файлов |

> ⚠️ **В production** все dev defaults должны быть переопределены через env vars / Vault.

## Лицензия

MIT
