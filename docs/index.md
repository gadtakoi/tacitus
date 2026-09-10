# Документация Tacitus

## Оглавление

- [Техническое задание](ToR_Tacitus_v1.0.md) — требования к сервису: роли, функции, данные, API, конфигурация, безопасность
- [Backend](backend.md) — FastAPI, архитектура, слои приложения, интеграция с MediaMTX
- [Frontend](frontend.md) — React, Vite, TypeScript, API клиент
- [Инфраструктура](infrastructure.md) — Docker Compose, Nginx, MinIO, MediaMTX, ротация архивов
- [Выгрузка файлов из S3](s3-download.md) — скачивание всего бакета через API и напрямую из MinIO
- [Тестирование](testing.md) — pytest, ruff, pre-commit, покрытие
- [Обновление версий модулей](upgrades.md) — зависимости, базовые образы, PostgreSQL, MediaMTX, регламент
- [Деплой](deployment.md) — Ansible, env vars, инициализация проекта (первый пользователь), роль MediaMTX, обновление
  пакетов ОС, проверка состояния сервера
