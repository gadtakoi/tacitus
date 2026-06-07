# Тестирование

## Инструменты

- **pytest** — unit и integration тесты
- **ruff** — линтинг и форматирование
- **pre-commit** — hooks перед коммитом
- **pytest-cov** — покрытие кода

## Запуск

```bash
# Все тесты
pytest tests/ -v

# Покрытие
pytest tests/ --cov=app --cov-report=term-missing

# Линтинг
ruff check .
ruff format .

# Pre-commit (все hooks)
pre-commit run --all-files
```

## Структура тестов

| Файл                              | Что тестируется                          |
|-----------------------------------|------------------------------------------|
| `test_hello.py`                   | Hello World endpoint                     |
| `test_health.py`                  | Health check                             |
| `test_auth.py`                    | Регистрация, логин, JWT, 401             |
| `test_files_router.py`            | Защищённые endpoints (mock S3)           |
| `test_s3_service.py`              | S3Service (mock boto3)                   |
| `test_mediamtx_archive_manager.py`| Ротация архивов MediaMTX (age + size)    |

## Покрытие

Текущее покрытие: **86%** (726 statements, 101 missed).
