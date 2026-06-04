# Backend

## Технологии

- **Python** 3.14+
- **FastAPI** — веб-фреймворк
- **Pydantic** — валидация и сериализация
- **python-jose** — JWT токены
- **passlib** — хеширование паролей (pbkdf2_sha256)
- **boto3** — клиент для S3 (MinIO)

## Архитектура

```
app/
├── api/
│   ├── dependencies.py      # JWT dependency (get_current_user)
│   └── routers/
│       ├── auth.py          # /auth/* endpoints
│       ├── files.py         # /files/* endpoints
│       └── hello.py         # / endpoint
├── core/
│   ├── config.py            # Pydantic-settings (env vars)
│   ├── exceptions.py        # AppError и обработчики
│   ├── logging_config.py    # Structured logging
│   └── middleware.py        # RequestIdMiddleware
├── schemas/
│   ├── auth.py              # RegisterRequest, LoginRequest, TokenResponse
│   ├── files.py             # FileUploadResponse, FileListResponse, ...
│   └── hello.py             # HelloWorldResponse
└── services/
    ├── auth_service.py      # Регистрация, логин, JWT lifecycle
    ├── hello_service.py     # Hello World business logic
    └── s3_service.py        # CRUD операции с MinIO
```

## Аутентификация

- Регистрация и логин через JSON body (не query params — security)
- JWT access token с TTL 30 минут
- Пароли хешируются через `pbkdf2_sha256` (passlib)
- In-memory хранилище пользователей (MVP)

## Настройка

```bash
pip install -e ".[dev]"
```

Переменные окружения (префикс `TACITUS_`):
- `JWT_SECRET` — обязательно переопределить в production
- `JWT_ALGORITHM` — по умолчанию `HS256`
- `JWT_EXPIRATION_MINUTES` — по умолчанию `30`
- `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`
