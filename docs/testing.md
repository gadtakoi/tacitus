# Тестирование

## Инструменты

- **pytest** — unit и integration тесты
- **anyio** — плагин для async-тестов (маркер `@pytest.mark.anyio`)
- **pytest-cov** — покрытие кода
- **ruff** — линтинг и форматирование
- **mypy** — статическая типизация
- **pre-commit** — hooks перед коммитом

Все они объявлены в `[project.optional-dependencies].dev`:

```bash
pip install -e ".[dev]"   # или: uv sync --all-extras
```

## Запуск

```bash
# Все тесты
pytest tests/ -v

# Покрытие
pytest tests/ --cov=app --cov-report=term-missing

# Линтинг
ruff check .
ruff format .

# Типы
mypy app

# Pre-commit (все hooks)
pre-commit run --all-files
```

## Требования к окружению

Часть тестов работает с реальной PostgreSQL. `conftest.py` проверяет
доступность базы один раз за сессию и **пропускает** зависимые тесты, если она
недоступна — так остальной набор проходит без Docker.

```bash
# Поднять только базу
docker compose up -d postgres

# Один раз создать тестовую базу
docker compose exec postgres psql -U tacitus -d tacitus \
  -c "CREATE DATABASE tacitus_test OWNER tacitus;"
```

Адрес по умолчанию — `postgresql+asyncpg://tacitus:tacitus@localhost:15432/tacitus_test`.
Порт **15432**, а не 5432: именно так compose пробрасывает postgres на хост
(`${POSTGRES_HOST_PORT:-15432}:5432`). Переопределяется через
`TACITUS_DATABASE_URL`.

> Порт публикуется **только на loopback**, но на обоих адресах — `127.0.0.1`
> и `[::1]`. Это не избыточность: на Windows `localhost` резолвится сначала в
> IPv6, и при публикации только на `127.0.0.1` каждое подключение ждёт ~2 с
> таймаута перед откатом на IPv4. Фикстуры используют `NullPool`, то есть
> открывают соединение на каждый тест, — прогон замедлялся с 78 секунд до
> восьми с лишним минут. Если тесты внезапно поползли, проверьте
> `docker port tacitus-postgres`: там должны быть обе строки.

> Без доступной PostgreSQL пропускается порядка 60 тестов — вся логика auth,
> репозиториев и миграций, — причём **молча**, обычным `skip`. Перед мержем
> убеждайтесь, что в итоговой строке pytest нет `skipped`, иначе изменения в
> `app/db/` и `app/services/auth_service.py` остаются непроверенными.

`conftest.py` подменяет **на время прогона** и тестовый engine, и engine
приложения на `NullPool`: каждый тест работает в собственном event loop (anyio
открывает по одному на тест), а пулированное соединение asyncpg остаётся
привязанным к тому циклу, в котором было создано. Без `NullPool` тест проходит
поодиночке и падает в общем прогоне.

> Это относится только к тестам. В работающем приложении
> `app/db/engine.py` использует пул по умолчанию с `pool_pre_ping=True` —
> см. `docs/backend.md` → «Пул соединений». Формулировка выше раньше
> утверждала, что `NullPool` стоит и в приложении; это было неверно и однажды
> увело диагностику не в ту сторону.

Схема создаётся через `Base.metadata.create_all` перед каждым тестом и
удаляется после; сессия привязана к транзакции, которая откатывается, поэтому
данные между тестами не протекают.

## Структура тестов

| Файл                                 | Что тестируется                                    | Нужна БД |
|--------------------------------------|----------------------------------------------------|----------|
| `test_health.py`                     | Health check                                       | нет      |
| `test_auth.py`                       | Регистрация, логин, refresh, logout, 401           | да       |
| `test_auth_schemas.py`               | Валидация username и пароля на уровне схем         | нет      |
| `test_repositories.py`               | Репозитории auth, атомарность отзыва токена        | да       |
| `test_migrations.py`                 | Применимость Alembic-миграций                      | да       |
| `test_files_router.py`               | Защищённые endpoints файлов (mock S3)              | да       |
| `test_s3_service.py`                 | `S3Service`, `sanitize_filename` (mock boto3)      | нет      |
| `test_stream_config.py`              | Схемы и сервис конфигурации потоков                | да       |
| `test_client_ip.py`                  | Определение IP клиента, подделка `X-Forwarded-For` | нет      |
| `test_rate_limit.py`                 | Лимиты и `429`                                     | да       |
| `test_cors.py`                       | CORS preflight, неизвестный origin                 | нет      |
| `test_middleware.py`                 | `MaxRequestSizeMiddleware`, порядок middleware     | нет      |
| `test_error_handling.py`             | Обработчики ошибок, отсутствие stack trace         | нет      |
| `test_logging.py`                    | `JsonFormatter`, request_id                        | нет      |
| `test_security_logging.py`           | `log_security_event`                               | нет      |
| `test_security_logging_integration.py` | Запись security-событий в БД                     | да       |
| `test_suspicious_activity.py`        | Детект всплесков 401/403                           | да       |
| `test_sentry.py`                     | Инициализация и фильтрация PII                     | нет      |
| `test_nginx_security.py`             | Security headers и наследование `add_header`       | нет      |
| `test_mediamtx_archive_manager.py`   | Ротация архивов MediaMTX (age + size)              | нет      |
| `test_startup_validation.py`         | Production-гарды `validate_startup_settings`       | нет      |
| `test_mediamtx_security.py`          | `authInternalUsers`, разделение прав, bind RTSP    | нет      |
| `test_streams_auth.py`               | Basic-аутентификация в MediaMTX Control API        | да       |
| `test_compose_pinning.py`            | Пиннинг образов, установок пакетов при старте нет  | нет      |
| `test_stream_simulator.py`           | Профиль, изоляция и секреты имитатора потока       | нет      |
| `test_dockerfile_pinning.py`         | Сборка образов по лок-файлам (Python и npm)        | нет      |
| `test_transaction_boundaries.py`     | Атомарность ротации токена, durability аудита      | да       |
| `test_streams_router.py`             | Отказы MediaMTX, контракт ответа, rate limit       | да       |
| `test_request_id.py`                 | Валидация `X-Request-Id`, log/header injection     | да       |
| `test_login_timing.py`               | Одна проверка пароля на любой исход логина         | да       |
| `test_password_bounds.py`            | Границы длины пароля, отсутствие эха в 422         | да       |
| `test_logout_authz.py`               | Владелец refresh-токена при выходе, лимиты         | да       |
| `test_db_reconnect.py`               | Восстановление пула после перезапуска БД           | да       |

## Симуляция видеопотока и E2E-проверка конвейера

Тесты выше не поднимают ни одного настоящего потока: `test_streams_router.py`
работает с замоканным MediaMTX, а `test_stream_simulator.py` разбирает
конфигурацию, не запуская контейнеров. Живой конвейер проверяется отдельно —
имитатором публикации и сквозным скриптом.

### Что нужно один раз

Пароль публикатора берётся из `.env` (compose подставляет его в сервис).
Если `.env` создан до появления имитатора, добавьте в него блок
`TACITUS_SIM_*` из `.env.example` — минимум две строки:

```bash
printf '
TACITUS_SIM_PUBLISH_PASSWORD=dev-publisher-change-me
TACITUS_SIM_READ_PASSWORD=dev-reader-change-me
' >> .env
```

Значения должны совпадать с учётными записями `publisher` и `reader` из
`mediamtx/mediamtx.yml`. Пароль намеренно не является опцией командной
строки: argv виден в выводе `ps` и сохраняется в истории оболочки.

### Запуск имитатора

Стек должен быть поднят (`docker compose up -d`).

```bash
scripts/simulate-stream.sh start
```

Один непрерывный поток в путь `cam1`. Команда возвращается только после того,
как MediaMTX действительно сообщил `ready`, иначе завершается с кодом 1 и
печатает хвост лога имитатора.

```bash
scripts/simulate-stream.sh start --paths cam1,live          # два потока сразу
scripts/simulate-stream.sh start --size 640x480 --fps 15 --bitrate 500k
scripts/simulate-stream.sh start --mode reconnect --up 5 --down 5 --cycles 3
scripts/simulate-stream.sh start --no-audio                 # только видео
```

Путь должен быть описан в `mediamtx/mediamtx.yml` (`cam1` и `live` в dev).
Публикация в необъявленный путь отвергается с `400 Bad Request`, поэтому
скрипт проверяет список путей заранее и перечисляет доступные.

### Наблюдение и остановка

```bash
scripts/simulate-stream.sh status      # ready, дорожки, рост принятых байт, файлы записи
scripts/simulate-stream.sh logs -f     # логи имитатора
scripts/simulate-stream.sh stop        # удалить контейнер, стек не трогая
```

`status` делает два замера принятых байт с интервалом 3 с: один замер показал
бы, что путь существует, но не что он живёт.

### Проверка, что MediaMTX отказывает

```bash
scripts/simulate-stream.sh negative
```

Три попытки, каждая обязана быть отвергнута: неверный пароль публикатора
(401), необъявленный путь (400) и учётная запись `reader`, у которой нет
действия `publish` (401). Код 0 — отказано во всех трёх; код 1 — MediaMTX
принял то, что обязан был отвергнуть; код 2 — до сервера не дошли, то есть
проверка не выполнена. Различать последние два важно: «сервер отказал» и
«сервера нет» дают у ffmpeg одинаковый ненулевой код, поэтому решение
принимается по stderr, а не по коду возврата.

### Сквозная проверка конвейера

```bash
scripts/e2e-stream-check.sh
```

Проходит всю цепочку и печатает отчёт в стиле `scripts/healthcheck.sh`:

| § | Что проверяется |
|---|---|
| 0 | Стек поднят, `docker`/`curl` есть, приложение отвечает на `/health` |
| 1 | Базовый замер числа файлов в `recordings/` |
| 2 | Имитатор поднялся и пути стали `ready` |
| 3 | Control API MediaMTX: `ready`, две дорожки на каждый путь |
| 4 | Регистрация и логин тестового пользователя, получение JWT |
| 5 | `GET /api/streams` отдаёт оба пути, ключи в snake_case |
| 6 | Принятые байты растут по каждому пути независимо |
| 7 | В `recordings/<путь>/` появляются новые файлы |
| 8 | Обрыв и переподключение: `ready` → не `ready` → `ready` |
| 9 | Негативный сценарий, и после него MediaMTX по-прежнему принимает поток |
| 10 | Только с `--with-archive`: выгрузка записей в S3 |

Коды выхода: `0` — FAIL нет, `1` — есть FAIL (или WARN при `--strict`),
`2` — проверку не удалось выполнить. Молчаливого пропуска здесь нет: если
предусловие не выполнено, это код 2, а не 0.

Имитатор останавливается через `trap … EXIT`, поэтому не переживает скрипт
даже при `Ctrl-C`.

`--with-archive` выключен по умолчанию: он запускает архиватор с
`MAX_AGE_DAYS=0`, то есть выгружает и **удаляет** все локальные записи, а
переход «запись → архив» необратим (`CONTEXT.md`). Работает только при
`TACITUS_ENVIRONMENT=development`.

### Почему это скрипт, а не pytest

`scripts/healthcheck.sh` документирован как «только читает состояние» и
направляется на production, а публикация потока — мутация: она создаёт записи
и, с `--with-archive`, объекты в S3. Модуль pytest, пропускающийся без Docker,
усугубил бы проблему молчаливых пропусков, описанную выше, а таймингам RTSP
внутри pytest свойственна нестабильность. Поэтому статические гарантии живут
в `test_stream_simulator.py` и выполняются всегда, а живой конвейер
проверяется скриптом, который запускают осознанно.

### Если записи не появляются

Самая частая причина FAIL в §7 — права на каталог. MediaMTX работает под
uid 1000, и если `./recordings` ему недоступен, поток принимается и отдаётся
нормально, а на диск не попадает ничего; ошибка видна только в логах
MediaMTX:

```bash
docker compose logs mediamtx | grep 'permission denied'
```

На Linux лечится `sudo chown -R 1000:1000 ./recordings`. На Windows права
bind-mount задаёт Docker Desktop, и каталог достаётся контейнеру как
`root:root` — запись в dev-окружении на Windows не работает, тогда как
остальной конвейер (публикация, `GET /streams`, отдача) проверяется полностью.
Сквозная проверка выводит эту подсказку сама, когда находит `permission
denied` в логах.

## Покрытие

Текущее покрытие: **91%** (1365 statements, 128 missed) — 263 теста, все
проходят.

Значение измеряется **с поднятой PostgreSQL** — без неё цифра занижена, потому
что пропущенные тесты не выполняют код `app/db` и `app/services/auth_service.py`.

```bash
docker compose up -d postgres
pytest tests/ --cov=app --cov-report=term-missing
```

Самые непокрытые места на сейчас:

| Модуль | Покрытие | Чего не хватает |
|--------|----------|-----------------|
| `db/engine.py` | 56% | Реальный `get_db_session` (в тестах подменяется) |
| `services/auth_service.py` | 72% | Ветки обработки ошибок БД (`AppError` на `DBAPIError`) |
| `core/exceptions.py` | 84% | Обработчики `integrity_error` / `dbapi_error` |
| `services/s3_service.py` | 84% | Ветки `ClientError` при list/upload/delete |
| `core/logging_config.py` | 85% | Ветки файлового хендлера и ротации |

Бейдж в `README.md` обновляется вручную по результату этой команды.
