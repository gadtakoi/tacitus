# План: Логирование и мониторинг

## Цель

Расширить систему логирования Tacitus: добавить ротацию файловых логов, управление через переменные окружения и опциональную интеграцию с Sentry.

---

## 1. Логирование в файлы и в stdout

### 1.1. RotatingFileHandler

- [x] Добавить `logging.handlers.RotatingFileHandler` в `app/core/logging_config.py`
- [x] Путь к лог-файлу: `/var/log/tacitus/app.log` (production) или `./logs/app.log` (development)
- [x] Максимальный размер файла: 10 МБ
- [x] Количество резервных копий: 5
- [x] Формат: JSON-structured logging (timestamp, level, message, request_id, logger_name)

### 1.2. StreamHandler для stdout

- [x] Сохранить существующий `logging.StreamHandler(sys.stdout)`
- [x] Убедиться, что stdout handler используется для Docker/контейнерных сред
- [x] Убедиться, что оба handler'а (file + stdout) могут работать одновременно

### 1.3. Отдельный security-лог

- [x] Добавить `RotatingFileHandler` для `security` logger (`app/core/security_logging.py`)
- [x] Путь: `/var/log/tacitus/security.log`
- [x] Отделить security события от основного application log

### 1.4. Создание директории для логов

- [x] Добавить `mkdir -p` (или `os.makedirs`) при старте приложения
- [x] Убедиться, что non-root пользователь (`tacitus`) имеет права на запись
- [x] Для Docker: создать volume `app-logs` и примонтировать в `/var/log/tacitus`

### 1.5. Тестирование

- [x] Проверить, что логи пишутся в файл при включённом файловом логировании
- [x] Проверить ротацию: создать файл > 10 МБ, убедиться, что создаётся `app.log.1`
- [x] Проверить, что security events попадают в `security.log`

---

## 2. Переключение через переменные окружения

### 2.1. Новые переменные в `app/core/config.py`

- [ ] `TACITUS_LOG_LEVEL` (default: `INFO`, варианты: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`)
- [ ] `TACITUS_LOG_FORMAT` (default: `json`, варианты: `json`, `text`)
- [ ] `TACITUS_LOG_DESTINATION` (default: `stdout`, варианты: `stdout`, `file`, `both`)
- [ ] `TACITUS_LOG_FILE_PATH` (default: `/var/log/tacitus/app.log`)
- [ ] `TACITUS_LOG_MAX_BYTES` (default: `10485760` — 10 МБ)
- [ ] `TACITUS_LOG_BACKUP_COUNT` (default: `5`)

### 2.2. Обновление `app/core/logging_config.py`

- [ ] `setup_logging()` должен читать настройки из `settings`
- [ ] В зависимости от `LOG_DESTINATION` добавлять `StreamHandler`, `RotatingFileHandler` или оба
- [ ] В зависимости от `LOG_FORMAT` использовать JSON-форматтер или plain text
- [ ] В зависимости от `LOG_LEVEL` устанавливать `root_logger.setLevel()`

### 2.3. Обновление `.env.example`

- [ ] Добавить все новые `TACITUS_LOG_*` переменные с дефолтными значениями
- [ ] Добавить комментарии с описанием каждой переменной

### 2.4. Обновление документации

- [ ] Добавить раздел "Логирование" в `docs/backend.md`
- [ ] Описать доступные переменные окружения и их значения
- [ ] Добавить пример `.env` для development и production

### 2.5. Тестирование

- [ ] Проверить `LOG_DESTINATION=stdout` — только stdout, файла нет
- [ ] Проверить `LOG_DESTINATION=file` — только файл, stdout пуст
- [ ] Проверить `LOG_DESTINATION=both` — и файл, и stdout
- [ ] Проверить `LOG_FORMAT=text` — plain text вместо JSON
- [ ] Проверить `LOG_LEVEL=DEBUG` — debug-сообщения видны

---

## 3. Опциональная интеграция с Sentry

### 3.1. Зависимости

- [ ] Добавить `sentry-sdk[fastapi]` в `pyproject.toml` (опциональная зависимость или dev)
- [ ] Обновить `requirements.txt` через `pip-compile --generate-hashes`

### 3.2. Конфигурация

- [ ] Добавить `TACITUS_SENTRY_DSN` в `app/core/config.py` (default: пустая строка)
- [ ] Добавить `TACITUS_SENTRY_ENVIRONMENT` (default: `development`)
- [ ] Добавить `TACITUS_SENTRY_TRACES_SAMPLE_RATE` (default: `0.1`, float 0.0–1.0)

### 3.3. Инициализация Sentry

- [ ] Создать `app/core/sentry.py` с функцией `init_sentry()`
- [ ] Инициализировать `sentry_sdk.init()` только если `TACITUS_SENTRY_DSN` не пустой
- [ ] Интеграция с FastAPI: `FastApiIntegration()`
- [ ] Передача `environment`, `traces_sample_rate`, `release` (версия из `pyproject.toml`)

### 3.4. Интеграция в `app/main.py`

- [ ] Вызвать `init_sentry()` сразу после `setup_logging()` (только если DSN задан)
- [ ] Убедиться, что Sentry ловит необработанные исключения в middleware и routers

### 3.5. Дополнительные настройки

- [ ] Добавить `before_send` hook для фильтрации PII (passwords, tokens) перед отправкой в Sentry
- [ ] Добавить тег `request_id` к Sentry scope для связи с логами
- [ ] Добавить пользовательский контекст (`username`) при аутентификации

### 3.6. Обновление `.env.example`

- [ ] Добавить `TACITUS_SENTRY_DSN=` (пустое по умолчанию — Sentry отключён)
- [ ] Добавить `TACITUS_SENTRY_ENVIRONMENT=development`
- [ ] Добавить `TACITUS_SENTRY_TRACES_SAMPLE_RATE=0.1`

### 3.7. Документация

- [ ] Добавить раздел "Sentry" в `docs/backend.md`
- [ ] Описать, как включить Sentry (заполнить `SENTRY_DSN`)
- [ ] Описать, какие данные отправляются и как фильтруются PII

### 3.8. Тестирование

- [ ] Проверить, что при пустом `SENTRY_DSN` приложение стартует без ошибок, Sentry не инициализируется
- [ ] Проверить, что при заполненном `SENTRY_DSN` ошибки отправляются в Sentry
- [ ] Проверить, что PII (пароли, токены) не попадают в Sentry events
- [ ] Проверить, что `request_id` присутствует в Sentry tags

---

## 4. Финальная верификация

- [ ] `ruff check .` проходит без ошибок
- [ ] `pytest tests/` — все тесты passed
- [ ] `pip-audit -r requirements.txt` — 0 уязвимостей
- [ ] Проверка в Docker: логи видны в `docker compose logs`, файлы создаются в volume
- [ ] Проверка Ansible-деплоя: лог-директория создаётся, права корректны
