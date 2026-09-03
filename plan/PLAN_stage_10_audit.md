# План: устранение замечаний аудита проекта

## 1. Цель

Устранить замечания, выявленные при полной проверке проекта на соответствие
правилам `.ai/system/*`, `.ai/project/*`, `.ai/modules/fastapi.md`,
`.ai/modules/docker-compose.md` и навыкам проекта (`ponytail-audit`,
`code-review`, `tdd`).

> **Важно:** этот план не объединяется с предыдущими `PLAN_stage_*.md` и с
> `PLAN.md`. Он описывает только работы по результатам аудита от 2026-09-03.

## 2. Исходное состояние (замер на 2026-09-03)

| Проверка | Результат на момент аудита |
|---|---|
| `ruff check app tests` | All checks passed |
| `ruff format --check app tests` | 59 files already formatted |
| `pytest tests/` | **1 failed, 166 passed** (`test_migrations.py`) |
| `pytest --cov=app` | 87% (1373 stmts, 185 missed) |
| `mypy app` | **не запускается — mypy не установлен в `.venv`** |
| Секреты в git | чисто, отслеживается только `.env.example` |
| `plan/PLAN.md` | выполнен полностью, ADR-011 на месте |

Слабые места покрытия: `app/api/routers/streams.py` — 36%,
`app/services/auth_service.py` — 67%, `app/db/engine.py` — 56%.

## 3. Пункты плана

### 3.1. Высокий приоритет

- [x] **A-01. MediaMTX без аутентификации.**
  В `mediamtx/mediamtx.yml` и `ansible/roles/mediamtx/templates/mediamtx.yml.j2`
  нет ни `authInternalUsers`, ни `publishUser`/`readUser`, при этом
  `docker-compose.yml` публикует порт `8554` на хост, а `record: yes` включена.
  - [x] Описать модель аутентификации RTSP (кто публикует, кто читает).
  - [x] Добавить `authInternalUsers` с разделением прав publish/read.
  - [x] Ограничить доступ к `8554` firewall/VPN, вынести порт в переменную.
  - [x] Добавить ADR о модели аутентификации MediaMTX (ADR-012).
  - [x] Обновить `docs/security.md` и `docs/infrastructure.md`.
  - [x] **Побочная находка:** Control API отвечал 401 на запросы бэкенда
    (дефолт выдаёт action `api` только loopback-адресам), то есть
    `GET /streams` в запущенном стеке был сломан и возвращал 502. Добавлена
    Basic-аутентификация бэкенда; проверено — endpoint отдаёт 200.

- [ ] **A-02. Docker-образ собирается без хеш-пиннинга.**
  `Dockerfile` ставит `uv pip install --system --no-cache "."` из
  `pyproject.toml` с `>=`-ограничениями; `requirements.txt` (997 хешей) в
  сборке не участвует, хотя `docs/security.md` §8.1 утверждает обратное.
  - [ ] Перевести сборку на `--require-hashes -r requirements.txt`.
  - [ ] Проверить, что образ собирается и стартует.
  - [ ] Привести `docs/security.md` §8.1 в соответствие с реальностью.

- [ ] **A-03. Установка зависимости из сети в рантайме.**
  Сервис `mediamtx-cleanup` в `docker-compose.yml` выполняет
  `pip install --no-cache-dir boto3` без версии при каждом старте.
  - [ ] Запиннить версию либо перевести сервис на образ `tacitus-app`.

- [x] **A-04. `TACITUS_DEBUG` не проверяется на production.**
  `validate_startup_settings()` контролирует `jwt_secret`, MinIO-креды и CORS,
  но не `debug`. При `debug=true` `generic_exception_handler` отдаёт клиенту
  `str(exc)`, а `create_async_engine(echo=True)` пишет SQL с параметрами в логи.
  - [x] Добавить проверку `debug` в `validate_startup_settings()`.
  - [x] Добавить negative-тест на запуск с `environment=production, debug=true`.
  - [x] Отразить требование в `docs/security.md`.

### 3.2. Средний приоритет

- [ ] **A-05. Границы транзакций размазаны по репозиториям.**
  `UserRepository.create`, `RefreshTokenRepository.create`/`revoke` коммитят
  сами. В `rotate_refresh_token` отзыв старого токена и сохранение нового —
  два отдельных коммита: при сбое второго пользователь теряет сессию без
  компенсации.
  - [ ] Вынести `commit()` из репозиториев на уровень сервиса/зависимости.
  - [ ] Добавить тест на атомарность ротации refresh-токена.

- [ ] **A-06. Мёртвая фича stream config и пароли в открытом виде.**
  `services/stream_config_service.py`, `db/repositories/streams.py`, таблица
  `streams` и `StreamCredentials` (~250 строк) не подключены ни к одному
  роутеру и используются только тестами; `publish_pass`/`read_pass` хранятся
  plaintext и возвращаются в `StreamConfig`.
  - [ ] Решить: удалить слой либо довести до API (см. A-01).
  - [ ] Если оставляем — не хранить пароли plaintext и не отдавать наружу.

- [x] **A-07. Падающий тест миграций.**
  `tests/test_migrations.py` вызывает `subprocess.run(["alembic", ...])` —
  бинарь ищется в `PATH`, поэтому при штатном запуске
  `.venv\Scripts\python -m pytest` тест падает с `FileNotFoundError [WinError 2]`.
  - [x] Заменить на `[sys.executable, "-m", "alembic", ...]`.
  - [x] Убедиться, что прогон `pytest tests/` полностью зелёный.

- [x] **A-08. Гейты качества настроены, но не исполняются.**
  `mypy`, `pip-audit`, `cyclonedx-bom`, `ansible-core` объявлены в
  `[project.optional-dependencies].dev` и задокументированы, но не установлены
  в `.venv`; в `.pre-commit-config.yaml` pytest / pip-audit / npm audit
  закомментированы; CI отсутствует.
  - [x] Установить dev-зависимости в `.venv`.
  - [x] Запустить `mypy app` и устранить найденные ошибки типизации.
  - [x] Запустить `pip-audit -r requirements.txt` и зафиксировать результат.
  - [ ] Включить `mypy` и `pip-audit` в pre-commit либо в CI.

- [ ] **A-21. 14 известных уязвимостей в 5 пакетах** (найдено при выполнении
  A-08, зафиксировано в `docs/security.md` §8.1).
  - [ ] `starlette` 1.2.1 → 1.3.1: PYSEC-2026-249 — `request.form()` не
    применяет `max_fields`/`max_part_size`. **Приоритетно:** касается
    `POST /files/upload`. Плюс PYSEC-2026-248 (`request.url` из
    невалидированного пути). Проверить совместимость с FastAPI.
  - [ ] `cryptography` 48.0.0 → 49.0.0+: GHSA-537c-gmf6-5ccf (OpenSSL в wheel)
    и PYSEC-2026-3552/3553/3554 (цепочки сертификатов, PKCS#7 — в приложении
    не используются).
  - [ ] `pyasn1` 0.6.3 → 0.6.4: PYSEC-2026-3455/3456/3457, транзитивно через
    `python-jose`.
  - [ ] `pydantic-settings` 2.14.1 → 2.14.2: GHSA-4xgf-cpjx-pc3j; затрагивает
    неиспользуемый `NestedSecretsSettingsSource`.
  - [ ] `ecdsa` 0.19.2 — PYSEC-2026-1325, **фикса нет**. Tacitus подписывает
    JWT алгоритмом HS256, ECDSA-путь не задействован → зафиксировать как
    принятый риск с обоснованием.
  - [ ] Перегенерировать `requirements.txt` через
    `pip-compile --generate-hashes` и повторить аудит.

- [ ] **A-09. Интеграция с MediaMTX без тестов и с шероховатостями.**
  `app/api/routers/streams.py` — 36% покрытия, ветки 502/503/504 не покрыты.
  - [ ] Добавить тесты на сетевую ошибку, timeout, 4xx/5xx от MediaMTX и на
    контракт ответа.
  - [ ] Расширить обработку исключений (`RemoteProtocolError`, `InvalidURL`,
    ошибка разбора JSON) вместо падения в 500.
  - [ ] Добавить rate limit на `/streams`.
  - [x] Убрать `"body": response.text` из лога (полный body внешнего ответа) —
    сделано попутно в A-01.

- [ ] **A-10. `X-Request-Id` принимается от клиента без валидации.**
  `get_request_id()` возвращает заголовок как есть, nginx его не
  перезаписывает: длина не ограничена (раздувание логов), при
  `log_format != "json"` возможна подделка строк лога.
  - [ ] Ограничить длину и набор символов, иначе генерировать свой UUID.
  - [ ] Добавить тест на некорректный `X-Request-Id`.

### 3.3. Низкий приоритет

- [ ] **A-11.** `authenticate_user` при неизвестном пользователе не считает
  хеш → timing-разница и user enumeration. Добавить dummy-verify.
- [ ] **A-12.** `RegisterRequest.password` / `LoginRequest.password` без
  `max_length` → CPU на pbkdf2 при большом пароле. Ограничить длину.
- [ ] **A-13.** `/auth/logout` — единственный auth-эндпоинт без
  `@limiter.limit`; refresh-токен из тела не сверяется с владельцем
  access-токена.
- [ ] **A-14.** `nginx/Dockerfile` копирует только `frontend/package.json` и
  выполняет `npm install` — `package-lock.json` игнорируется, сборка фронта
  невоспроизводима. Перейти на `npm ci` с lock-файлом.
- [ ] **A-15.** Плавающие теги образов `postgres:16-alpine`,
  `redis:7-alpine`, `python:3.14-slim` — не `latest`, но и не зафиксированы
  (`docker-compose.md`: «задавать версии»).
- [ ] **A-16.** `postgres` публикует `15432:5432` на хост — в production
  лишняя экспозиция; вынести публикацию в dev-override.
- [ ] **A-17.** Дрейф двух nginx-конфигов: `nginx/nginx.conf` (только `:80`,
  HSTS без TLS, `/hls/` и `/webrtc/` с `Access-Control-Allow-Origin *`) против
  `ansible/roles/ssl_cert/templates/nginx-ssl.conf.j2` (без hls/webrtc).
  HLS/WebRTC в MediaMTX выключены, проксирование мёртвое; комплект
  security-заголовков продублирован трижды.
- [ ] **A-18.** Порядок middleware: `RequestIdMiddleware` добавлен первым и
  потому самый внутренний — ответы 429 от `SlowAPIMiddleware` уходят без
  заголовка `x-request-id`.
- [ ] **A-19.** Переусложнение и леса (`ponytail`):
  - [ ] `GET /` = «Hello World» (`routers/hello.py`, `hello_service.py`,
    `schemas/hello.py`, тесты) — демо-леса в production.
  - [ ] `validate_name`/`validate_source` продублированы дословно в
    `StreamConfig` и `StreamCreateRequest`.
  - [ ] `release="tacitus@0.1.0"` в `sentry.py` хардкодом дублирует версию из
    `pyproject.toml`.
  - [ ] `_to_schema(model)` без аннотации типа и с локальным импортом уже
    импортированного `StreamConfig`.

### 3.4. Отдельно (не требуется правилами)

- [ ] **A-20.** В проекте нет `CONTEXT.md` с доменным словарём. При смешанной
  русско-английской терминологии это заметный пробел для онбординга.

## 4. Критерии приёмки

- [ ] Все пункты раздела 3.1 закрыты либо явно отложены с указанием причины.
- [x] `pytest tests/` проходит полностью, без падающих тестов.
- [x] `mypy app` запускается и завершается без ошибок.
- [x] `pip-audit -r requirements.txt` выполнен, результат зафиксирован.
- [x] `ruff check .` — без замечаний; `ruff format --check app tests` — чисто.

  > `ruff format --check .` (команда из `plan/PLAN.md` §3.7) даёт
  > «1 file would be reformatted» — это `plan/PLAN_stage_5.md`: ruff форматирует
  > ```python-блоки внутри markdown. К коду отношения не имеет; либо
  > ограничить команду в документации до `app tests`, либо поправить сниппет
  > в историческом плане.
- [ ] Документация (`docs/security.md`, `docs/infrastructure.md`,
  `docs/testing.md`) соответствует фактическому состоянию кода.
- [ ] Для изменённых моделей безопасности добавлены ADR в `plan/DECISIONS.md`.

## 5. Журнал выполнения

| Дата | Пункт | Что сделано |
|------|-------|-------------|
| 2026-09-03 | — | Проведён аудит, составлен план |
| 2026-09-03 | A-04 | В `validate_startup_settings()` добавлен запрет `debug` при `environment=production`. Создан `tests/test_startup_validation.py` (7 тестов: happy path production, все четыре гарда, development не валидируется). Обновлены `docs/security.md` §2.6, §4.3 и deployment-checklist |
| 2026-09-03 | A-07 | `tests/test_migrations.py` переведён на `[sys.executable, "-m", "alembic", ...]` — тест больше не зависит от `PATH`. Прогон зелёный |
| 2026-09-03 | A-08 | Установлены dev-зависимости (`uv pip install -e ".[dev]"`): mypy 2.3.1, pip-audit 2.10.1, cyclonedx-bom, ansible-core. `mypy app tests` — **Success: no issues found in 60 source files**. `pip-audit -r requirements.txt` — 14 уязвимостей, результат зафиксирован в `docs/security.md` §8.1 и вынесен в пункт A-21. Подпункт «включить в pre-commit/CI» остаётся открытым |
| 2026-09-03 | A-21 | Новый пункт, заведён по результату `pip-audit` |
| 2026-09-03 | A-01 | Явный `authInternalUsers` в `mediamtx/mediamtx.yml` и в ansible-шаблоне: `publisher` (publish), `reader` (read/playback), `tacitus-api` (api). Бэкенд ходит в Control API с HTTP Basic (`TACITUS_MTX_API_USER/PASSWORD`), 401/403 логируется отдельно и отдаётся как 502 `MediaMTX API authentication failed`. RTSP-порт по умолчанию биндится на `127.0.0.1` (`TACITUS_MTX_RTSP_BIND`). Гарды против dev-паролей: `validate_startup_settings()` + pre-check в `ansible/playbook.yml`. Новые тесты `test_mediamtx_security.py` (8) и `test_streams_auth.py` (4). ADR-012, `docs/security.md` §2.8, `docs/infrastructure.md`. Покрытие `streams.py` 36% → 67%, `config.py` 78% → 100% |
| 2026-09-03 | A-01 | Проверено на живом стеке (MediaMTX 1.15.1): анонимные publish/read/api → 401; `publisher` публикует, но читать не может → 401; `reader` читает живой поток; `GET /streams` через nginx → 200 (до изменения 502). Негативная проверка самих тестов: три варианта плохого конфига (нет секции, запись `any`, единый всемогущий аккаунт) отлавливаются |
| 2026-09-03 | — | Контрольный прогон: `pytest tests/` — 174 passed (было 166 passed + 1 failed); покрытие 87% (1376 stmts, 175 missed); `ruff check` и `ruff format --check` — чисто. Обновлены `docs/testing.md` (число тестов, таблица файлов) |
| 2026-09-03 | — | Итоговый прогон после A-01: `pytest tests/` — **187 passed**; покрытие **88%** (1384 stmts, 164 missed); `ruff check app tests`, `ruff format --check app tests`, `mypy app tests` — чисто |
