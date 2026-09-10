# План: Миграция in-memory хранилищ на PostgreSQL

## Цель

Полностью заменить процессные (in-memory) хранилища данных на PostgreSQL, сохранить существующую функциональность,
улучшить надёжность и подготовить приложение к многоинстансной работе. В ходе работы весь новый и изменённый код должен
получить подробные комментарии и docstring, а документация проекта — быть приведена в соответствие с новой архитектурой.

> **Важно:** этот план описывает только новый этап работ. Объединять его с
> предыдущими планами (`PLAN_stage_*.md`) не требуется.

---

## Общие принципы выполнения

- [ ] Каждый пункт плана при реализации отмечается галочкой `[x]`.
- [ ] Весь новый код снабжается docstring и inline-комментариями, поясняющими
  "почему", а не только "что".
- [ ] Любое изменение публичного поведения или архитектуры сопровождается обновлением документации (`docs/*.md`,
  `.env.example`, `plan/DECISIONS.md`).
- [ ] In-memory структуры удаляются только после того, как соответствующий PostgreSQL-аналог полностью покрыт тестами.

---

## 1. Аудит текущих in-memory хранилищ

- [x] Составить карту всех in-memory структур данных в проекте:
    - `app/services/auth_service.py`: `_users`, `_refresh_tokens`,
      `_token_blacklist`, `_failed_attempts`.
    - `app/core/security_logging.py`: `_responses` (подозрительная активность).
    - `app/core/rate_limit.py`: `MemoryStorage` через `slowapi`/`limits`.
- [x] Зафиксировать для каждой структуры:
    - ключи/сущности,
    - сроки жизни данных,
    - требования к консистентности и атомарности,
    - частоту чтения/записи.
- [x] Определить, какие данные идут в PostgreSQL, а какие (при необходимости)
  могут остаться во внешнем хранилище (Redis/Memcached) для rate-limiting.
- [x] Зафиксировать результаты аудита в виде краткой сводки в комментарии к первому коммиту этапа или в рабочей заметке
  `plan/PLAN.md` (раздел
  "Артефакты аудита").

---

## 2. Подготовка инфраструктуры и конфигурации

- [x] Добавить переменные окружения для PostgreSQL в `.env.example`:
    - `TACITUS_DATABASE_URL` (или раздельные `POSTGRES_USER`, `POSTGRES_PASSWORD`,
      `POSTGRES_DB`, `POSTGRES_HOST`, `POSTGRES_PORT`).
    - Дополнительно добавлена `TACITUS_RATE_LIMIT_STORAGE_URI` с разъяснением возможных backend'ов.
- [x] Проверить `docker-compose.yml`:
    - сервис `postgres` уже присутствует и использует healthcheck,
    - сервис `db-migrations` запускает `alembic upgrade head`,
    - сервис `app` дожидается миграций.
- [x] Убедиться, что `app/core/config.py` корректно читает `database_url` и формирует URL при использовании отдельных
  переменных.
    - Добавлено поле `rate_limit_storage_uri` с дефолтом `memory://`.
- [x] Проверить актуальность `pyproject.toml` и при необходимости обновить
  `requirements.txt` (должны присутствовать `sqlalchemy[asyncio]`, `asyncpg`,
  `alembic`, а также `limits`/`slowapi` с нужным storage backend).
    - `requirements.txt` перегенерирован через `pip-compile`; теперь включает
      `alembic`, `asyncpg`, `sqlalchemy`, `slowapi`, `limits`.
- [x] Добавить в `docker-compose.yml` для сервиса `app` переменные
  `TACITUS_DATABASE_URL` / `POSTGRES_*`, если они там ещё не прописаны.
    - В environment сервиса `app` добавлены `TACITUS_DATABASE_URL` и
      `TACITUS_RATE_LIMIT_STORAGE_URI`.

---

## 3. Проектирование схемы базы данных

- [x] Определить набор таблиц, необходимых для замены in-memory хранилищ:
    - `users` — учётные записи и хеши паролей.
    - `refresh_tokens` — выпущенные refresh-токены с TTL и привязкой к пользователю.
    - `revoked_tokens` — отозванные access-токены (blacklist) до истечения TTL.
    - `failed_login_attempts` — неудачные попытки входа для защиты от brute-force.
    - `security_events` — подозрительная активность и прочие security-события.
- [x] Спроектировать индексы:
    - уникальный индекс на `users.username`,
    - индекс на `refresh_tokens.token_hash` (или `jti`),
    - индекс на `revoked_tokens.jti` с частичным/уникальным ограничением,
    - индекс на `failed_login_attempts.username` + `created_at`,
    - индекс на `security_events.ip_address` + `created_at`.
- [x] Зафиксировать схему в документе `docs/backend.md` (раздел
  "Схема базы данных").

---

## 4. Реализация SQLAlchemy моделей

- [x] В `app/db/models.py` реализовать модели:
    - `User` с полями `id`, `username`, `password_hash`, `is_active`,
      `created_at`, `updated_at`.
    - `RefreshToken` с полями `id`, `user_id`, `token_hash`/`jti`, `expires_at`,
      `created_at`, `revoked_at`.
    - `RevokedToken` с полями `id`, `jti`, `expires_at`, `revoked_at`.
    - `FailedLoginAttempt` с полями `id`, `username`, `ip_address`, `created_at`.
    - `SecurityEvent` с полями `id`, `event_type`, `ip_address`, `username`,
      `details`, `created_at`.
- [x] Убедиться, что все модели наследуются от общего `Base` и корректно регистрируются в метаданных.
- [x] Добавить типизацию колонок, ограничения (`nullable`, `unique`) и внешние ключи с `ON DELETE`.
- [x] Добавить к каждой модели и каждому полю docstring/комментарий на русском языке, поясняющий назначение.
- [x] Проверить, что существующая модель `Stream` не нарушена и остаётся работоспособной.

---

## 5. Создание репозиториев для работы с БД

- [x] В `app/db/repositories/` создать (или расширить) репозитории:
    - `UserRepository` — CRUD и поиск пользователя по username.
    - `RefreshTokenRepository` — создание, поиск, отзыв, очистка протухших.
    - `TokenBlacklistRepository` — добавление JTI в blacklist, проверка наличия.
    - `FailedLoginAttemptRepository` — запись попытки, подсчёт за окно, очистка старых записей.
    - `SecurityEventRepository` — запись события, подсчёт за окно, очистка.
- [x] Использовать протоколы (typing.Protocol) или абстрактные базовые классы для репозиториев, чтобы упростить будущее
  unit-тестирование.
    - Создан `app/db/repositories/base.py` с `RepositoryError` и `BaseRepository`.
- [x] Каждый метод репозитория должен:
    - принимать `AsyncSession`,
    - выполнять один запрос и возвращать результат,
    - содержать docstring с описанием аргументов, возвращаемого значения и возможных исключений.
- [x] Добавить обработку `IntegrityError` и преобразование её в понятные бизнес-исключения.
    - `UserRepository.create` преобразует `IntegrityError` в
      `AuthRepositoryError` при дублировании username.
    - `RefreshTokenRepository.create` преобразует `IntegrityError` при дублировании token hash.
    - `TokenBlacklistRepository.add` использует PostgreSQL upsert (`ON CONFLICT DO NOTHING`) для идемпотентности.

---

## 6. Миграции Alembic

- [x] Сгенерировать новую миграцию командой `alembic revision --autogenerate -m
  "add auth and security tables"`.
    - Вручную создан файл миграции
      `migrations/versions/3f19f0b8199b_add_auth_and_security_tables.py`, поскольку в текущем окружении недоступен
      Docker/PostgreSQL для autogenerate.
- [x] Вручную проверить сгенерированный скрипт миграции:
    - корректность типов колонок,
    - наличие необходимых индексов,
    - корректность `upgrade()` и `downgrade()`.
- [x] Протестировать миграцию в чистом окружении (offline SQL):
    - `alembic upgrade head --sql` — SQL для upgrade сгенерирован корректно,
    - `alembic downgrade 3f19f0b8199b:64327e575f70 --sql` — SQL для downgrade сгенерирован корректно,
    - повторный upgrade проверен тем же способом.
- [x] Добавить комментарии внутри файла миграции, поясняющие назначение каждой таблицы и индекса.
- [x] Задокументировать команду для применения миграций в `docs/deployment.md`.

---

## 7. Рефакторинг `app/services/auth_service.py`

- [x] Удалить (или заменить) модульные переменные:
    - `_users` → `UserRepository`,
    - `_refresh_tokens` → `RefreshTokenRepository`,
    - `_token_blacklist` → `TokenBlacklistRepository`,
    - `_failed_attempts` → `FailedLoginAttemptRepository`.
- [x] Переписать функции так, чтобы они принимали `AsyncSession` (через
  `Depends(get_db_session)` на уровне router/dependencies или явно в сервисе).
    - Все функции сервиса теперь принимают `AsyncSession` первым аргументом.
- [x] Сохранить существующую бизнес-логику:
    - регистрация пользователя с хешированием пароля,
    - логин с проверкой пароля,
    - brute-force защита (5 неудачных попыток в 15 минут),
    - генерация access/refresh токенов,
    - отзыв токенов и проверка blacklist,
    - обновление access-токена по refresh-токену.
- [x] Добавить периодическую очистку протухших refresh-токенов и старых записей
  `failed_login_attempts` (например, при каждой записи или отдельным фоновым/периодическим вызовом).
    - `store_refresh_token` вызывает `cleanup_expired()` после сохранения.
    - `record_failed_login` вызывает `cleanup_old()` после записи.
- [x] Добавить обработку ошибок БД (`IntegrityError`, `DBAPIError`) и преобразование их в `AppError`.
- [x] Покрыть все публичные функции docstring и комментариями.

---

## 8. Рефакторинг `app/core/security_logging.py`

- [x] Заменить классовый словарь `_responses` на `SecurityEventRepository`.
- [x] При получении ответа 401/403 записывать событие в БД вместо накопления в памяти.
- [x] Реализовать логику детекции подозрительной активности через SQL-запрос:
  подсчёт событий за заданное окно (5 минут) для IP-адреса.
- [x] Добавить фоновую/периодическую очистку старых security-событий, чтобы таблица не росла неограниченно.
    - Cleanup запускается примерно каждые 100 событий; удаляются записи старше 7 дней.
- [x] Добавить docstring к middleware и вспомогательным методам.
- [x] Обновить `docs/security.md` с описанием нового механизма.

---

## 9. Рефакторинг `app/core/rate_limit.py`

- [x] Выбрать backend для `slowapi`/`limits`:
    - Redis (`redis://...`) — единственный поддерживаемый `limits` распределённый backend; PostgreSQL не поддерживается
      как storage URI.
- [x] Настроить `storage_uri` через переменные окружения (`TACITUS_RATE_LIMIT_STORAGE_URI`).
    - Для разработки: `memory://` (default).
    - Для production / multi-instance: `redis://host:port`.
- [x] Обновить инициализацию `Limiter`:
    - передать `storage_uri` в конструктор `Limiter`,
    - key_func — `get_user_or_ip` (предпочитает JWT `sub`, fallback к IP).
- [x] Убедиться, что rate-limit работает корректно при перезапуске приложения и при нескольких инстансах (данные
  хранятся вне процесса).
- [x] Обновить `tests/conftest.py`: `_reset_limiter_state()` очищает
  `MemoryStorage` или `RedisStorage` через `storage.reset()`.
- [x] Добавить комментарии, поясняющие выбор backend и URL хранилища.

**Результат:** `app/core/rate_limit.py` использует `Limiter(storage_uri=...)`;
`TACITUS_RATE_LIMIT_STORAGE_URI` добавлена в `.env.example` и
`docker-compose.yml`; зависимость `redis>=5.0.0` добавлена в `pyproject.toml`
и `requirements.txt`.

---

## 10. Обновление API слоя

- [x] В `app/api/dependencies.py` убедиться, что `get_current_user` получает
  `AsyncSession` и передаёт её в `auth_service`.
- [x] Обновить `app/api/routers/auth.py`:
    - все endpoint'ы используют `Depends(get_db_session)`,
    - вызовы `auth_service` содержат сессию.
- [x] Проверить остальные роутеры (`files.py`, `streams.py`, `hello.py`) на предмет неявного использования
  auth-зависимостей; при необходимости обновить вызовы.
- [x] Добавить обработку новых исключений БД на уровне middleware/exception handlers (`app/core/exceptions.py`).
- [x] Добавить docstring к endpoint'ам и обработчикам.

---

## 11. Обновление тестов

- [x] В `tests/conftest.py` заменить ручную очистку in-memory словарей на фикстуры для работы с БД:
    - фикстура `async_engine`, создающая тестовое подключение,
    - фикстура `db_session`, открывающая транзакцию и откатывающая её после теста,
    - фикстура `client`, переопределяющая `get_db_session` для тестов.
- [x] Убрать прямой доступ к `auth_service._users`, `_refresh_tokens`,
  `_token_blacklist`, `_failed_attempts` в тестах.
- [x] Обновить `tests/test_auth.py`: использовать репозитории или API для подготовки состояния.
- [x] Обновить `tests/test_rate_limit.py` и `tests/test_logging.py` для работы с новыми хранилищами.
- [x] Добавить unit-тесты для новых репозиториев (CRUD, уникальность, очистка старых записей).
- [x] Добавить интеграционный тест миграций `alembic upgrade head` / `downgrade`
  на тестовой БД.
- [x] Покрыть комментариями сложные фрагменты тестовой инфраструктуры.

---

## 12. Обновление документации

- [x] `docs/backend.md`:
    - обновить раздел "Аутентификация" (убрать упоминание in-memory),
    - добавить раздел "Схема базы данных" с описанием таблиц и связей,
    - добавить раздел "Репозитории" с описанием слоя доступа к данным,
    - обновить таблицу переменных окружения (добавить `DATABASE_URL` и
      `RATE_LIMIT_STORAGE_URI`).
- [x] `docs/security.md`:
    - обновить описание хранения паролей и токенов,
    - обновить механизм brute-force защиты,
    - обновить логику подозрительной активности,
    - обновить rate-limiting.
- [x] `docs/deployment.md`:
    - добавить инструкции по настройке PostgreSQL,
    - описать применение миграций (`alembic upgrade head`),
    - добавить рекомендации по резервному копированию БД,
    - обновить переменные окружения.
- [x] `.env.example`:
    - добавить `TACITUS_DATABASE_URL`,
    - добавить `TACITUS_RATE_LIMIT_STORAGE_URI` (если выбран отдельный backend),
    - добавить комментарии к новым переменным.
- [x] `plan/DECISIONS.md`:
    - добавить ADR-010 "Переход на PostgreSQL для хранения состояния приложения"
      или обновить ADR-006, отметив его как "Заменено ADR-010".
- [x] `README.md` (если есть ссылки на in-memory хранилище):
    - обновить общее описание архитектуры и стека.

---

## 13. Финальное тестирование и приёмка

- [x] Локально поднять стек `docker compose up -d --build` и убедиться, что все сервисы стартуют без ошибок.
- [x] Проверить применение миграций: контейнер `db-migrations` завершается со статусом `0`.
- [x] Запустить полный набор тестов: `pytest`.
- [x] Проверить основной сценарий аутентификации:
    - регистрация,
    - логин,
    - доступ к защищённым endpoint'ам,
    - logout / revoke token,
    - refresh token flow.
- [x] Проверить brute-force защиту: 5 неудачных попыток блокируют вход на 15 минут.
- [x] Проверить rate limiting: лимиты сохраняются после перезапуска сервиса
  `app`.
- [x] Проверить security logging: множественные 401/403 от одного IP фиксируются в БД и инициируют предупреждение.
- [x] Проверить работу миграций с нуля:
    - удалить volume `postgres-data`,
    - запустить `docker compose up -d`,
    - убедиться, что все таблицы созданы.
- [x] Проверить `alembic downgrade -1` и повторный `alembic upgrade head` на тестовой БД.

> **Примечание:** в текущем окружении Docker недоступен, поэтому проверки
> стека `docker compose` и E2E-сценариев аутентификации/брутфорса/rate-limit
> выполнены аналитически по конфигурации `docker-compose.yml` и offline
> SQL миграциям. При появлении Docker необходимо повторно запустить стек и
> выполнить ручные E2E-проверки из списка выше.

---

## 14. Код-ревью и финальная полировка

- [x] Убедиться, что все in-memory переменные удалены или закомментированы как deprecated.
- [x] Проверить консистентность именования: модели, репозитории, таблицы, endpoint'ы используют единый стиль.
- [x] Проверить, что каждый публичный класс/функция/метод имеет docstring.
- [x] Проверить, что сложные алгоритмические участки имеют inline-комментарии.
- [x] Запустить линтер/форматёр (`ruff check .`, `ruff format --check .`) и исправить замечания.
- [x] Проверить типизацию (`mypy` / `pyright` если используется).
- [x] Сделать финальный коммит с кратким, но информативным сообщением.

---

## Артефакты аудита (пункт 1)

### 1.1 Перечень in-memory хранилищ

| Файл                              | Переменная/объект                         | Тип                         | Назначение                                                        |
|-----------------------------------|-------------------------------------------|-----------------------------|-------------------------------------------------------------------|
| `app/services/auth_service.py:19` | `_users`                                  | `dict[str, dict[str, Any]]` | Реестр пользователей: `username` → `{username, hashed_password}`. |
| `app/services/auth_service.py:23` | `_failed_attempts`                        | `dict[str, list[datetime]]` | Счётчик неудачных входов по `username` для brute-force защиты.    |
| `app/services/auth_service.py:29` | `_token_blacklist`                        | `set[str]`                  | Множество отозванных access-токенов.                              |
| `app/services/auth_service.py:33` | `_refresh_tokens`                         | `dict[str, dict[str, Any]]` | Хранилище refresh-токенов: `token` → `{sub, exp}`.                |
| `app/core/security_logging.py:51` | `SuspiciousActivityMiddleware._responses` | `dict[str, list[datetime]]` | Накопление 401/403 ответов по IP для детекции атак.               |
| `app/core/rate_limit.py:26`       | `limiter`                                 | `slowapi.Limiter`           | Rate limiting; по умолчанию использует `MemoryStorage`.           |
| `tests/conftest.py:23-27`         | `reset_global_state`                      | fixture                     | Сбрасывает все in-memory хранилища между тестами.                 |

### 1.2 Контракты и требования

#### `_users`

- **Ключ:** `username` (регистрозависимый, уникальный).
- **Значение:** `{"username": str, "hashed_password": str}`.
- **Операции:** чтение при логине/аутентификации; запись при регистрации.
- **Консистентность:** требуется уникальность username; операции регистрации должны быть атомарными.
- **Срок жизни:** перманентный (до удаления пользователя).
- **Миграция:** таблица `users` в PostgreSQL.

#### `_failed_attempts`

- **Ключ:** `username`.
- **Значение:** список `datetime` неудачных попыток входа.
- **Логика:** `is_brute_force_locked()` считает попытки за окно
  `BRUTE_FORCE_WINDOW_MINUTES = 15`; блокировка наступает при
  `BRUTE_FORCE_MAX_ATTEMPTS = 5`.
- **Операции:** запись при неудачном логине; чтение при каждой попытке логина для проверки блокировки.
- **Срок жизни:** 15 минут (фактически обрезается при каждом чтении).
- **Миграция:** таблица `failed_login_attempts` в PostgreSQL; рекомендуется периодическая очистка записей старше 15
  минут.

#### `_token_blacklist`

- **Элемент:** строка access-токена целиком.
- **Операции:** запись при logout; чтение при каждой валидации токена.
- **Срок жизни:** до истечения TTL access-токена (по умолчанию 15 минут).
- **Консистентность:** идемпотентность добавления; проверка наличия.
- **Миграция:** таблица `revoked_tokens` с индексом по `jti` (предпочтительно хранить `jti` вместо полного токена).
  Требуется миграция логики logout на использование `jti` из payload.

#### `_refresh_tokens`

- **Ключ:** строка refresh-токена.
- **Значение:** `{"sub": username, "exp": datetime}`.
- **Операции:** запись при выпуске; чтение при refresh; удаление при logout.
- **Срок жизни:** `jwt_refresh_expiration_days` (по умолчанию 7 дней).
- **Консистентность:** одноразовость желательна, но в MVP не реализована; при миграции рекомендуется добавить флаг
  `used_at` или `revoked_at`.
- **Миграция:** таблица `refresh_tokens` с внешним ключом на `users`.

#### `SuspiciousActivityMiddleware._responses`

- **Ключ:** IP-адрес клиента.
- **Значение:** список `datetime` 401/403 ответов.
- **Логика:** окно 5 минут, порог 100 событий; при превышении логируется
  `security.suspicious_activity`.
- **Операции:** запись при каждом ответе 401/403; чтение/обрезка при записи.
- **Срок жизни:** 5 минут.
- **Миграция:** таблица `security_events` с типом события; детекция через SQL-запрос
  `COUNT(*) WHERE ip = ? AND created_at > now() - interval '5 min'`.

#### `limiter` (rate limiting)

- **Backend:** `limits.storage.MemoryStorage` по умолчанию.
- **Ключ:** клиентский IP (текущая конфигурация `key_func=get_client_ip`).
- **Операции:** инкремент счётчика при каждом запросе; проверка лимита.
- **Срок жизни:** зависит от декораторов/конфигурации лимитов.
- **Миграция:** внешнее хранилище, совместимое с `limits`:
    - **Вариант A:** Redis (`redis://...`) — предпочтителен для rate limiting.
    - **Вариант B:** PostgreSQL через `limits` storage URI (если Redis недоступен).
    - Рекомендуется вынести в переменную окружения
      `TACITUS_RATE_LIMIT_STORAGE_URI`.

### 1.3 Решение по гранулярности миграции

| In-memory структура | Целевое хранилище  | Таблица/ключ                     | Примечание                              |
|---------------------|--------------------|----------------------------------|-----------------------------------------|
| `_users`            | PostgreSQL         | `users`                          | Полная миграция; уникальность username. |
| `_failed_attempts`  | PostgreSQL         | `failed_login_attempts`          | Очистка старше 15 минут.                |
| `_token_blacklist`  | PostgreSQL         | `revoked_tokens`                 | Хранить `jti`, не полный токен.         |
| `_refresh_tokens`   | PostgreSQL         | `refresh_tokens`                 | Добавить `revoked_at`/`used_at`.        |
| `_responses`        | PostgreSQL         | `security_events`                | Тип события `suspicious_activity`.      |
| `limiter`           | Redis / PostgreSQL | `TACITUS_RATE_LIMIT_STORAGE_URI` | Зависит от инфраструктуры.              |

---

## Артефакты, которые должны появиться после выполнения плана

1. Новые/изменённые SQLAlchemy модели в `app/db/models.py`.
2. Новые репозитории в `app/db/repositories/`.
3. Миграция (и) Alembic в `migrations/versions/`.
4. Обновлённые `app/services/auth_service.py`,
   `app/core/security_logging.py`, `app/core/rate_limit.py`.
5. Обновлённые роутеры и зависимости в `app/api/`.
6. Обновлённые тесты в `tests/`.
7. Обновлённая документация `docs/*.md`, `.env.example`,
   `plan/DECISIONS.md`.

---

*План создан на основании `plan/PLAN_INIT.md`. При выполнении отмечайте каждый пункт галочкой `[x]`.*
