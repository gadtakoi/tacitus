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

- [x] **A-02. Docker-образ собирается без хеш-пиннинга.**
  `Dockerfile` ставил `uv pip install --system --no-cache "."` из
  `pyproject.toml` с `>=`-ограничениями; `requirements.txt` в сборке не
  участвовал, хотя `docs/security.md` §8.1 утверждал обратное.
  - [x] **Блокер, обнаруженный при выполнении:** `requirements.txt` был не
    просто неиспользуемым, а нерабочим — в нём отсутствовали `sqlalchemy`,
    `uvicorn`, `python-jose`, `sentry-sdk`. Сборка по нему дала бы образ,
    который не стартует, поэтому пункт нельзя было выполнить как записан.
  - [x] Источником истины назначен `uv.lock`; `requirements.txt` генерируется
    из него (`uv export --frozen --no-dev --no-emit-project`): 52 пакета, все
    с хешами, все объявленные runtime-зависимости покрыты.
  - [x] Сборка переведена на `pip install --require-hashes -r requirements.txt`.
    Убраны два неучтённых сетевых захода: неприпнутый `pip install uv` и
    установка проекта как дистрибутива (PEP 517 source build тянул
    `setuptools` мимо хеш-пиннинга). Каталог `app/` копируется в образ.
  - [x] `pre-commit` убран из `[project.dependencies]` — dev-инструмент,
    продублированный в dev-extras, попадал в runtime-образ.
  - [x] Образ собирается и стартует: версии совпадают с лок-файлом
    (`boto3 1.43.83`, `sqlalchemy 2.0.52`, `starlette 1.6.0`), `/health`,
    `/auth/me`, `/api/files/`, `/api/streams` отвечают 200.
  - [x] `docs/security.md` §8.1 приведён в соответствие с фактом;
    `docs/uv.md` переписан под процедуру обновления зависимостей.
  - [x] Регрессионные тесты `tests/test_dockerfile_pinning.py` (4),
    негативная проверка на 5 вариантах поломки.

- [x] **A-03. Установка зависимости из сети в рантайме.**
  Сервис `mediamtx-cleanup` в `docker-compose.yml` выполняет
  `pip install --no-cache-dir boto3` без версии при каждом старте.
  - [x] Промежуточный шаг: пин `boto3`/`botocore` на версии из
    `requirements.txt` — снят вместе с самой установкой (см. ниже).
  - [x] **Сервис переведён на образ `tacitus-app:0.1.0`.** Установка пакетов
    при старте убрана полностью: `boto3` приходит из сборки образа, контейнер
    не зависит от доступности PyPI и использует тот же SDK, что и приложение.
    Пиннинг остаётся ровно в одном месте — сборке образа (см. A-02).
    Снята и проблема транзитивных пакетов: они больше не резолвятся в рантайме.
  - [x] Отключён унаследованный `HEALTHCHECK` образа приложения: он проверяет
    HTTP-порт 8000, которого в этом контейнере нет, и контейнер уходил в
    `unhealthy`.
  - [x] Регрессионные тесты `tests/test_compose_pinning.py` (3): в командах
    сервисов нет `pip install`, cleanup работает на образе приложения,
    healthcheck отключён.

- [x] **A-04. `TACITUS_DEBUG` не проверяется на production.**
  `validate_startup_settings()` контролирует `jwt_secret`, MinIO-креды и CORS,
  но не `debug`. При `debug=true` `generic_exception_handler` отдаёт клиенту
  `str(exc)`, а `create_async_engine(echo=True)` пишет SQL с параметрами в логи.
  - [x] Добавить проверку `debug` в `validate_startup_settings()`.
  - [x] Добавить negative-тест на запуск с `environment=production, debug=true`.
  - [x] Отразить требование в `docs/security.md`.

### 3.2. Средний приоритет

- [x] **A-05. Границы транзакций размазаны по репозиториям.**
  `UserRepository.create`, `RefreshTokenRepository.create`/`revoke` коммитили
  сами. В `rotate_refresh_token` отзыв старого токена и сохранение нового —
  два отдельных коммита: при сбое второго пользователь терял сессию без
  компенсации.
  - [x] Вынести `commit()` из репозиториев на уровень сервиса. Репозитории
    теперь только стейджат работу (`flush` там, где INSERT обязан дойти до БД
    ради констрейнта или генерируемых значений), транзакцию закрывает сервис.
    Из `repositories/auth.py` и `repositories/streams.py` убрано 13 коммитов
    и 3 отката.
  - [x] **Граница выбрана на уровне сервиса, а не зависимости
    `get_db_session`.** Коммит в зависимости («commit на успехе, rollback на
    исключении») тихо сломал бы два сценария, обязанных сохраняться именно
    при неуспешном запросе: запись неудачного входа (эндпоинт сразу отдаёт
    401 — откат обнулил бы brute-force защиту) и отзыв всего семейства
    токенов при детекте повторного использования (запрос завершается 401,
    откат отменил бы реакцию на кражу токена).
  - [x] `logout` сделан атомарным: отзыв access- и refresh-токена шёл двумя
    транзакциями, и при сбое второй access оказывался отозван, а refresh нет —
    клиент продолжал выпускать новые access-токены. Добавлен
    `auth_service.logout_user()` с одним коммитом.
  - [x] Коммит добавлен в `SuspiciousActivityMiddleware`: он ведёт собственную
    сессию, а её репозиторий больше не коммитит сам.
  - [x] Добавить тест на атомарность ротации refresh-токена —
    `tests/test_transaction_boundaries.py` (4 теста) на настоящих
    транзакциях, а не в savepoint-фикстуре, которая откатывает всё и потому
    поведение коммитов не наблюдает. Проверено, что тест различает старое и
    новое поведение: с возвращённым самокоммитящим `revoke` предъявленный
    токен становится непригоден.

- [x] **A-06. Мёртвая фича stream config и пароли в открытом виде.**
  `services/stream_config_service.py`, `db/repositories/streams.py`, таблица
  `streams` и `StreamCredentials` (~250 строк) не подключены ни к одному
  роутеру и используются только тестами; `publish_pass`/`read_pass` хранятся
  plaintext и возвращаются в `StreamConfig`.
  - [x] Решить: удалить слой либо довести до API (см. A-01).
    **Решение владельца проекта:** убрать только credentials, задел под CRUD
    оставить — он остаётся основанием для `PLAN_stage_8_mediamtx.md` §3.1.2.
  - [x] Не хранить пароли plaintext и не отдавать наружу — поля удалены из
    модели, схем и БД (миграция `b2d8f6a3c714`), ADR-013.
  - [x] **Установленный факт:** в MediaMTX 1.15.1 нет per-path
    `publishUser`/`publishPass`/`readUser`/`readPass` (проверено по
    `GET /v3/config/pathdefaults/get`: 82 параметра, из auth-related только
    SRT-passphrases). Колонки описывали несуществующий контракт.

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

- [x] **A-21. 14 известных уязвимостей в 5 пакетах** (найдено при выполнении
  A-08). **Закрыто попутно в A-02:** перегенерация `requirements.txt` из
  `uv.lock` подняла версии, и `pip-audit` даёт **1 уязвимость в 1 пакете**.
  Старый лок-файл фиксировал версии годичной давности — отсюда и весь список.
  - [x] `starlette` 1.2.1 → 1.6.0: PYSEC-2026-248, PYSEC-2026-249 закрыты.
  - [x] `cryptography` 48.0.0 → 48.0.1+: GHSA-537c-gmf6-5ccf и три PYSEC.
  - [x] `pyasn1` 0.6.3 → 0.6.4, `pydantic-settings` 2.14.1 → 2.14.2+.
  - [x] `ecdsa` 0.19.2 — PYSEC-2026-1325, фикса по-прежнему нет. Остаётся
    принятым риском: приходит транзитивно через `python-jose`, а Tacitus
    подписывает JWT алгоритмом HS256 (HMAC), ECDSA-путь не задействован.
    Зафиксировано в `docs/security.md` §8.1.
  - [x] Аудит и сборка теперь используют один и тот же файл, поэтому
    проверенные версии совпадают с установленными.

<details>
<summary>Исходный список уязвимостей (для истории)</summary>
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

</details>

- [x] **A-09. Интеграция с MediaMTX без тестов и с шероховатостями.**
  `app/api/routers/streams.py` — было 36% покрытия, ветки 502/503/504 не
  покрыты. **Стало 100%.**
  - [x] Добавить тесты на сетевую ошибку, timeout, 4xx/5xx от MediaMTX и на
    контракт ответа — `tests/test_streams_router.py` (17 тестов).
  - [x] Расширить обработку исключений вместо падения в 500: добавлен
    catch-all `(httpx.HTTPError, httpx.InvalidURL)` → 502, покрывающий
    `RemoteProtocolError`, `UnsupportedProtocol`, `TooManyRedirects` и
    некорректный `TACITUS_MTX_API_URL`. `InvalidURL` в httpx **не**
    наследуется от `HTTPError`, поэтому указан отдельно. Добавлена обработка
    не-JSON тела и несоответствия схеме → 502.
  - [x] Валидация ответа перенесена в `_fetch_from_mediamtx` (обобщён по
    модели): иначе дрейф контракта MediaMTX всплывал бы как 500 на
    сериализации `response_model`.
  - [x] Добавить rate limit на `/streams` — 60/минуту на пользователя для
    обоих endpoints, как у `GET /files/`.
  - [x] Убрать `"body": response.text` из лога (полный body внешнего ответа) —
    сделано попутно в A-01.
  - [x] Проверено, что тесты различают старое и новое поведение: при снятом
    catch-all ровно четыре случая (RemoteProtocolError, UnsupportedProtocol,
    TooManyRedirects, InvalidURL) отдают 500 и тесты падают.

- [x] **A-23. `/streams` отвечает в camelCase** (обнаружено при A-09).
  `pageCount`, `confName`, `bytesReceived` — в отличие от остального API,
  который весь в snake_case. Схема хранит имена MediaMTX как алиасы для
  разбора входящего JSON, а FastAPI сериализует по алиасам
  (`response_model_by_alias=True` по умолчанию), и они утекали наружу.
  - [x] Решение: привести к snake_case. Обоснование — `coding-rules`
    требуют сохранять стиль проекта, а camelCase здесь возник побочно, а не
    как осознанный выбор; потребитель Tacitus не должен знать, что один
    endpoint проксирует стороннюю систему.
  - [x] Проверено отсутствие потребителей перед изменением: во фронтенде
    `/streams` не используется, в коде обращений к `pageCount`/`confName` нет
    (только устаревший артефакт в `build/`, не отслеживаемый git).
  - [x] На обоих маршрутах указан `response_model_by_alias=False`. Алиасы
    остались для разбора входящего JSON MediaMTX.
  - [x] Тест `test_responses_carry_no_camelcase_aliases` закрепляет настройку:
    её потеря при будущей правке вернёт алиасы наружу незаметно.
  - [x] Обновлены `docs/backend.md` и `docs/E2E_CHECKLIST.md` (там контракт
    был зафиксирован в старом виде).

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

- [x] **A-22. Расхождение в план-документах** (обнаружено при A-06).
  `plan/PLAN_INIT.md:8` утверждал «План `plan/PLAN_stage_8_mediamtx.md`
  выполнен», тогда как в самом файле был отмечен 1 пункт из 77:
  CRUD-эндпоинтов потоков, страницы UI и RTSPS не существует.
  - [x] Привести `PLAN_INIT.md` в соответствие с фактом: строка про stage 8
    заменена на «выполнен частично» плюс таблица состояния по разделам
    §3.1.1–§3.2 с указанием, что фактически сделано вне плана (прокси к
    Control API из stage 7, аутентификация MediaMTX из ADR-012).
  - [x] Отметить в stage 8 пункты §3.1.1, фактически выполненные в stage 9.
    Все восемь сверены с кодом, а не со словами stage 9; зафиксированы три
    отклонения от исходной формулировки: `postgres:16-alpine` вместо
    `18-alpine`, удалённые колонки credentials (ADR-013), имена методов
    репозитория без суффикса `_stream`.
  - [x] Переписать §3.1.3. Добавлена таблица параметров, которые **реально**
    настраиваются per-path (запись, источник, ограничения, хуки — сверено по
    `GET /v3/config/pathdefaults/get`), а разграничение доступа переформулировано
    через `authInternalUsers` с `permissions[{action, path}]`. Отдельно
    зафиксирована проблема доставки учётных данных без перезапуска: файл
    перечитывается только при старте, а env-переменные дописывают записи, а не
    заменяют их.

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
| 2026-09-03 | A-06 | Удалены `StreamCredentials` и поле `credentials` из `StreamConfig`/`StreamCreateRequest`/`StreamUpdateRequest`; из модели `Stream` убраны `publish_user`/`publish_pass`/`read_user`/`read_pass`; миграция `b2d8f6a3c714` (upgrade/downgrade проверены на тестовой БД, колонки уходят и возвращаются). Задел под CRUD сохранён по решению владельца. Попутно `_to_schema` получил аннотацию типа и лишился локального импорта (часть A-19). ADR-013, `docs/backend.md` |
| 2026-09-03 | A-07 | Доработка: `test_migrations.py` теперь дропает `alembic_version` перед прогоном. Тест зависел от предыдущего состояния БД — после ручного `alembic upgrade` он падал, потому что `Base.metadata.drop_all` не удаляет таблицу версий и upgrade считал базу уже на head. Проверено двумя подряд прогонами |
| 2026-09-04 | A-03 | `mediamtx-cleanup` пиннит `boto3==1.43.24` и `botocore==1.43.24` (версия из `requirements.txt`, вынесена в `TACITUS_CLEANUP_BOTO3_VERSION`). Проверено пересозданием контейнера: `Successfully installed boto3-1.43.24 botocore-1.43.24`, скрипт отрабатывает. При первой попытке сложенный YAML-скаляр сохранил переносы строк и `pip install` остался без аргументов — поймано запуском, команда собрана в одну строку. Тест `test_compose_pinning.py` (3), негативная проверка регекса на 6 формах записи |
| 2026-09-04 | A-03 | Сервис `mediamtx-cleanup` переведён с `python:3.14-slim` на `tacitus-app:0.1.0`; `pip install` при старте убран, `TACITUS_CLEANUP_BOTO3_VERSION` удалён из `.env.example`. Отключён унаследованный healthcheck (проверял несуществующий здесь HTTP-порт 8000 — поймано по `failing streak: 1` в `docker inspect`). Проверено end-to-end на изолированном volume с правами production (каталог `1000:1000`): от uid 1000 файл выгружен в S3 и удалён локально. Тесты переписаны под новое свойство, негативная проверка на 4 регрессиях. Тестовые артефакты (объект в MinIO, volume) удалены |
| 2026-09-04 | A-03 | Замечание по окружению: `./recordings` на dev-хосте принадлежит `root:root` (так монтирует Docker Desktop), поэтому ни mediamtx, ни cleanup не могут туда писать. Проблема предсуществующая и локальная — ansible создаёт каталог как `1000:1000` |
| 2026-09-04 | A-02 | Подтверждение дрейфа: `requirements.txt` пинит `boto3==1.43.24`, а в образе `tacitus-app` фактически стоит 1.43.87 — лок-файл в сборке не участвует |
| 2026-09-04 | A-02 | `requirements.txt` перегенерирован из `uv.lock` (`uv export`), сборка переведена на `pip install --require-hashes`, убраны `pip install uv` и установка проекта как дистрибутива, `pre-commit` вынесен из runtime-зависимостей. Образ пересобран и проверен по четырём эндпоинтам. Переписаны `docs/uv.md` и `docs/security.md` §8.1. Тесты `test_dockerfile_pinning.py` (4) + негативная проверка на 5 поломках |
| 2026-09-04 | A-21 | Закрыт попутно: `pip-audit` — 1 уязвимость вместо 14. Остался `ecdsa` без фикса как принятый риск |
| 2026-09-04 | A-06 | Исправление к записи от 2026-09-03: dev-база на head приведена **не была** — прогон падал, ненулевой код возврата был принят за безобидный. Причина предсуществующая: в базе `tacitus` отсутствовала таблица `streams` при `alembic_version = a1c7e4d2b905`, то есть схема разошлась с историей миграций. Таблица восстановлена DDL из `64327e575f70`, данные не тронуты; версия теперь `b2d8f6a3c714` |
| 2026-09-05 | A-09 | Обработка отказов MediaMTX расширена: catch-all `(httpx.HTTPError, httpx.InvalidURL)` → 502, не-JSON тело → 502, несоответствие схеме → 502. Валидация перенесена в `_fetch_from_mediamtx` (обобщён по модели через PEP 695). Rate limit 60/мин на оба endpoint. Новые тесты `test_streams_router.py` (17), покрытие роутера 36% → **100%**, общее 89% → **90%**. Негативная проверка: без catch-all четыре транспортных случая дают 500 |
| 2026-09-05 | A-09 | Проверено на живом стеке после пересборки образа: `GET /streams` 200 с контрактом camelCase (`confName=cam1`), `GET /streams/cam1` 200, несуществующий поток 404, без токена 401, rate limit сработал (59×200, далее 429). Тестовые данные и счётчики лимитов очищены |
| 2026-09-05 | A-22 | `PLAN_INIT.md`: stage 8 помечен как выполненный частично, добавлена таблица состояния по разделам. В stage 8 проставлены отметки §3.1.1 (все восемь сверены с кодом, зафиксированы три отклонения). §3.1.3 переписан: перечислены реально существующие per-path параметры MediaMTX 1.15.1, разграничение доступа переведено на `authInternalUsers` |
| 2026-09-05 | A-23 | Контракт `/streams` приведён к snake_case через `response_model_by_alias=False` на обоих маршрутах; алиасы оставлены для разбора входящего JSON MediaMTX. Проверено на живом стеке: `{"page_count":1,"item_count":2,...,"conf_name":"cam1","bytes_received":0,...}`, ни одного алиаса в теле. Обновлены `docs/backend.md` и `docs/E2E_CHECKLIST.md`. Тестов 214 → 215 |
| 2026-09-05 | A-23 | Новый пункт: `/streams` отвечает в camelCase из-за сериализации по алиасам; форма зафиксирована тестом, менять не стал — ломающее изменение контракта |
| 2026-09-05 | A-05 | Проверено на живом стеке после пересборки образа: ротация refresh-токена 200, повтор старого токена 401, новый токен после детекта reuse тоже 401 (отзыв семьи пережил неуспешный запрос); запись неудачного входа сохранилась при 401 (0 → 1 строка в `failed_login_attempts`); logout отозвал оба токена — и access, и refresh дают 401. Аудит из middleware пишется (8 событий). Тестовые данные удалены |
| 2026-09-05 | A-05 | Коммиты вынесены из репозиториев в сервисы (13 коммитов и 3 отката удалены; вместо них `flush`). Ротация refresh-токена стала атомарной: отзыв старого стейджится и коммитится вместе с сохранением нового. `logout_user()` объединил отзыв access- и refresh-токена в одну транзакцию. Явные коммиты сохранены там, где запись обязана пережить неуспешный запрос: `record_failed_login`, `_revoke_all_user_tokens`, аудит в `SuspiciousActivityMiddleware`. Новые тесты `test_transaction_boundaries.py` (4), покрытие `repositories/auth.py` и `repositories/streams.py` — 100% |
| 2026-09-04 | — | Инцидент окружения: диск C заполнился до нуля (в том числе из-за повторных пересборок образа), Docker перестал отвечать, `git status` выдавал неполный список. Файлы не пострадали. Освобождено пользователем |
| 2026-09-03 | A-22 | Новый пункт: `PLAN_INIT.md` объявляет stage 8 выполненным, в самом файле отмечен 1 пункт из 77 |
| 2026-09-03 | — | Итоговый прогон после A-01: `pytest tests/` — **187 passed**; покрытие **88%** (1384 stmts, 164 missed); `ruff check app tests`, `ruff format --check app tests`, `mypy app tests` — чисто |
| 2026-09-03 | — | Итоговый прогон после A-06: `pytest tests/` — **186 passed** (минус 1 тест: два теста credentials заменены одним, проверяющим их отсутствие); покрытие **89%** (1363 stmts, 156 missed); `ruff`, `mypy` — чисто. `repositories/streams.py` — 100% |
