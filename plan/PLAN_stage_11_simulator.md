# План проекта Tacitus — имитация загрузки видеопотока на сервер

> **Статус: выполнен.** Начат и завершён 2026-09-06.
>
> Все пункты закрыты. Сквозная проверка прогонена на живом стеке: 18 OK,
> 2 FAIL — оба в §7 «Запись на диск», и это **не дефект имитатора**, а
> ранее не выявленная проблема dev-окружения: MediaMTX работает под
> uid 1000, а bind-mount `./recordings` на Windows достаётся контейнеру
> как `root:root`, поэтому запись не велась никогда. Подробности —
> в разделе 9.

## 1. Цель

Сегодня проверить конвейер приёма видео можно только вручную: нужен либо
настоящий источник (камера), либо однострочная команда `ffmpeg`, которая
существует лишь как пример в тексте `plan/PLAN_stage_7_mediamtx.md:178` и
`plan/PLAN_stage_8_mediamtx.md:204`. Из-за этого весь путь
**публикация → путь MediaMTX → `GET /streams` → запись на диск → архив в S3**
никогда не проверялся автоматически: `docs/E2E_CHECKLIST.md` §6 описывает
проверку словами, а `pytest tests/` не поднимает ни одного потока.

Дать проекту воспроизводимую «фальш-камеру» — имитатор публикации RTSP-потока —
и автоматическую сквозную проверку конвейера на её основе.

**Конечный результат:**

- сервис `stream-simulator` в `docker-compose.yml` под профилем `simulator`;
- скрипт-обёртка `scripts/simulate-stream.sh`;
- сквозная проверка `scripts/e2e-stream-check.sh`;
- тесты `tests/test_stream_simulator.py`, работающие **без Docker**;
- разделы в документации, переменные в `.env.example`, ADR-014.

> **Важно:** план не объединяется с `plan/PLAN.md` и прочими
> `PLAN_stage_*.md`. CRUD-эндпоинты потоков (`PLAN_stage_8` §3.1.2) в объём
> не входят.

## 2. Текущее состояние (аудит)

Проверено эмпирически на поднятом стеке (только чтение), 2026-09-06:

| Факт | Значение |
|---|---|
| Образ `bluenviron/mediamtx:1.15.1-ffmpeg` | несёт `ffmpeg 6.1.2` (`--enable-libx264`, `--enable-avfilter`), `/bin/sh` (busybox), `nc`, `wget`. **Нет bash, curl, jq, python** |
| Протоколы | включён только RTSP; Control API `:9997` наружу не публикуется |
| Учётные записи | `publisher` (publish), `reader` (read+playback), `tacitus-api` (api) |
| Объявленные пути | ровно `cam1` и `live`; catch-all пути нет |
| Публикация в необъявленный путь | `Server returned 400 Bad Request`, записи не создаются |
| Публикация с неверным паролем | `method ANNOUNCE failed: 401 Unauthorized` |
| Профили compose | **не используются ни одним сервисом** — этот будет первым |
| Деплой | `ansible/roles/app_deploy/tasks/main.yml:130` → `docker compose up -d --build` **без** `--profile`; `.env.j2` не содержит пароля публикатора |
| Последний ADR | ADR-013 → следующий свободный **ADR-014** |

Два факта определяют почти все решения: **в образе нет bash и python** (точка
входа обязана быть POSIX `sh`), и **существуют только `cam1` и `live`** (отсюда
набор путей многопоточного сценария и бесплатный негативный случай).

## 3. Архитектура

```
                    ┌──────────────────────────────────┐
                    │ scripts/simulate-stream.sh       │  ← вход оператора
                    │ start / stop / status / logs /   │
                    │ negative                         │
                    └───────────────┬──────────────────┘
                                    │ docker compose --profile simulator
                                    ▼
  ┌───────────────────────┐   RTSP/TCP   ┌──────────────────┐
  │ stream-simulator      │─────────────▶│ mediamtx         │
  │ образ mediamtx:…      │  publisher@  │ paths: cam1,live │
  │ entrypoint = sh-скрипт│  mediamtx:   │ record: fmp4     │
  │ ffmpeg testsrc2+sine  │  8554/cam1   └────────┬─────────┘
  └───────────────────────┘                       │
                                     ┌────────────┴────────────┐
                                     ▼                         ▼
                          ./recordings/<path>/*.mp4    Control API :9997
                                     │                         │
                                     ▼                         ▼
                          mediamtx-cleanup → S3        app: GET /streams
                                     │                         │
                                     └──────────┬──────────────┘
                                                ▼
                                   scripts/e2e-stream-check.sh
```

### Компоненты

| Компонент | Файл | Роль |
|---|---|---|
| Сервис имитатора | `docker-compose.yml` | Контейнер под профилем `simulator` |
| Точка входа | `scripts/simulate-stream-entrypoint.sh` | POSIX `sh`: сценарии и вызовы ffmpeg |
| Обёртка | `scripts/simulate-stream.sh` | CLI оператора |
| Сквозная проверка | `scripts/e2e-stream-check.sh` | Проверка всего конвейера |
| Статические тесты | `tests/test_stream_simulator.py` | Гарантии без Docker |

## 4. Этапы реализации

### 4.1. Точка входа имитатора

Файл: `scripts/simulate-stream-entrypoint.sh`.

- [x] Написан на **POSIX `sh`** (`#!/bin/sh`, `set -eu`; `pipefail` в busybox
      ash отсутствует). Прецедент `scripts/mediamtx-archive-manager.py`
      неприменим: в образе нет python, а установка зависимостей в рантайме
      запрещена (`tests/test_compose_pinning.py`).
- [x] Русскоязычная шапка в стиле `scripts/init.sh`: назначение, режимы, коды
      выхода (`0` — штатно, `1` — сценарий не выполнен, `2` — окружение
      непригодно).
- [x] Хелперы `log()`, `die()`, `publish_url()`, `redacted_url()`. В лог
      уходит только URL с паролем, заменённым на `***`.
- [x] Preflight: пустой `TACITUS_SIM_PUBLISH_PASSWORD` → код 2; `nc -z` до
      MediaMTX → код 2; неизвестный режим → код 2.
- [x] Разбор `TACITUS_SIM_PATHS` через `IFS=,`; валидация имён по алфавиту
      `[A-Za-z0-9_-]` — тому же, что `_validate_stream_name` в
      `app/schemas/streams.py`.
- [x] Режим `continuous`: по одному фоновому ffmpeg на путь, `wait` по PID'ам
      с сохранением ненулевого кода.
- [x] Режим `reconnect`: цикл «публикуем `UP_SECONDS` → пауза
      `DOWN_SECONDS`» с учётом `TACITUS_SIM_CYCLES` (`0` — бесконечно).
- [x] Режим `negative` (см. 4.4).
- [x] `trap … TERM INT` перед `wait`: чистое отключение публикатора вместо
      SIGKILL через 10 с.
- [x] Backoff в `continuous`: перезапуск через 2 с, выход с кодом 1 после 5
      отказов за 60 с.

### 4.2. Команды ffmpeg

- [x] `publish_once <path> <duration|0> <user> <password>` собирает argv через
      `set --`.
- [x] `-re` перед **каждым** входом `lavfi`.
- [x] `-g = FPS * GOP_SECONDS`, `-keyint_min`, `-sc_threshold 0`.
- [x] `-pix_fmt yuv420p`.
- [x] Звук отдельной дорожкой (`sine` + `aac`); `TACITUS_SIM_AUDIO=false`
      убирает второй вход и блок `-c:a`.
- [x] `-rtsp_transport tcp` — соответствует `MTX_RTSPTRANSPORTS=tcp`.
- [x] `-loglevel error` зафиксирован.

### 4.3. Сервис в docker-compose.yml

- [x] Сервис `stream-simulator` после `mediamtx-cleanup`.
- [x] `profiles: [simulator]` — первый профиль в проекте.
- [x] `image: bluenviron/mediamtx:1.15.1-ffmpeg` — тот же образ, что у
      `mediamtx`.
- [x] `entrypoint: ["/bin/sh", "/simulate-stream-entrypoint.sh"]`.
- [x] `restart: "no"`, `read_only: true`, `cap_drop: [ALL]`,
      `user: "1000:1000"`, `tmpfs: [/tmp]`, лимиты `deploy.resources`.
- [x] Без `env_file`; каталог `./recordings` не монтируется; скрипт
      смонтирован `:ro`.
- [x] `depends_on: mediamtx: {condition: service_healthy}`.
- [x] Healthcheck не объявляется (обоснование — в комментарии).
- [x] Переменные `TACITUS_SIM_*` перечислены в `environment`.

### 4.4. Негативный сценарий

- [x] Режим `negative` как одноразовый прогон, не постоянный сервис.
- [x] Три проверки: неверный пароль → `cam1`; верный пароль → необъявленный
      путь; `reader` → `cam1`.
- [x] Позитивный контроль (`nc -z`) до негативных проверок.
- [x] Классификация по stderr (`rejected` / `unreachable` / `accepted` /
      `unknown`), а не по коду возврата ffmpeg.
- [x] Код возврата ffmpeg `8` намеренно не проверяется.
- [x] `unknown` → код 2: непонятная ошибка не является пройденной проверкой.

### 4.5. Обёртка scripts/simulate-stream.sh

- [x] Команды `start`, `stop`, `status`, `logs [-f]`, `negative`.
- [x] Опции `start`: `--paths`, `--mode`, `--size`, `--fps`, `--bitrate`,
      `--no-audio`, `--up`, `--down`, `--cycles`, `--wait`.
- [x] Коды выхода `0` / `1` / `2`.
- [x] Пароль не является опцией CLI (argv виден в `ps`).
- [x] Preflight по `/v3/config/paths/list` с внятным сообщением.
- [x] `mtx_api()` через `docker compose exec -T app python -`.
- [x] `start` дожидается `ready: true` или возвращает код 1 с хвостом логов.
- [x] `status` печатает `ready`, два замера `bytes_received`, `tracks`, число
      файлов записи.
- [x] `stop` — `rm -sf stream-simulator`, никогда не `down`.
- [x] `start` печатает оценку скорости записи.

### 4.6. Сквозная проверка scripts/e2e-stream-check.sh

- [x] Стиль `scripts/healthcheck.sh`: `ok`/`warn`/`bad`/`note`, секции, итог,
      `--strict`. Коды выхода `0` / `1` / `2`, молчаливого пропуска нет.
- [x] Хелпер `wait_until` вместо `sleep` наугад.
- [x] §0 предусловия → §1 базовый замер → §2 старт → §3 `ready` → §4 JWT →
      §5 `GET /api/streams` → §6 рост `bytes_received` → §7 файлы записи →
      §8 переподключение → §9 негативный сценарий.
- [x] §10 `--with-archive`: выключен по умолчанию, требует
      `TACITUS_ENVIRONMENT=development`, печатает предупреждение о
      необратимости перехода «запись → архив».
- [x] `trap … EXIT` останавливает имитатор даже при Ctrl-C.
- [x] Охранная проверка в `scripts/healthcheck.sh` (секция 5): контейнер
      `tacitus-stream-simulator` на сервере — это FAIL.

### 4.7. Тесты без Docker

Файл: `tests/test_stream_simulator.py`. Идиома — `tests/test_compose_pinning.py`
и `tests/test_mediamtx_security.py`. Выполняются **всегда**, без пропусков.

- [x] `test_simulator_is_profile_gated`
- [x] `test_only_the_simulator_is_profile_gated`
- [x] `test_simulator_shares_the_mediamtx_image`
- [x] `test_simulator_overrides_the_image_entrypoint`
- [x] `test_simulator_does_not_read_the_env_file`
- [x] `test_simulator_credentials_come_from_environment`
- [x] `test_simulator_has_no_access_to_recordings`
- [x] `test_simulator_does_not_restart_automatically`
- [x] `test_entrypoint_targets_posix_sh`
- [x] `test_entrypoint_never_prints_the_password`
- [x] `test_entrypoint_pins_ffmpeg_loglevel`
- [x] `test_simulator_env_vars_are_documented`
- [x] `test_production_deploy_does_not_enable_the_profile`
- [x] `test_healthcheck_flags_the_simulator`

### 4.8. Конфигурация и документация

- [x] `.env.example` — блок `TACITUS_SIM_*` после «MediaMTX archive limits».
- [x] **Не** добавлять `TACITUS_SIM_*` в
      `ansible/roles/app_deploy/templates/.env.j2` (второй барьер).
- [x] `docs/infrastructure.md` — раздел `## Симулятор потока` и строка в
      `## Security`.
- [x] `docs/testing.md` — раздел `## E2E-проверка конвейера потоков` и строка
      в таблице структуры тестов.
- [x] `docs/E2E_CHECKLIST.md` — подраздел «6.1 Симуляция публикации» и
      `## 13. Автоматическая E2E-проверка потока`.
- [x] `docs/deployment.md` — примечание о dev-only переменных.
- [x] `README.md` — одна строка про `scripts/simulate-stream.sh`.
- [x] `plan/DECISIONS.md` — ADR-014.

## 5. Требования

### Функциональные (FR)

- **FR-001**: имитатор публикует синтетический RTSP-поток в MediaMTX без
  ffmpeg на хосте и без настоящей камеры.
- **FR-002**: поддержаны сценарии `continuous`, несколько путей одновременно,
  `reconnect`, `negative`.
- **FR-003**: `scripts/simulate-stream.sh` управляет имитатором и возвращает
  ненулевой код, если поток не поднялся.
- **FR-004**: `scripts/e2e-stream-check.sh` подтверждает весь конвейер: путь
  `ready` → `GET /streams` с растущим `bytes_received` → файлы fmp4 в
  `recordings/` → (опционально) архив в S3.

### Нефункциональные (NFR)

- **NFR-001**: имитатор не стартует без явного профиля и не попадает в
  production — зафиксировано тестами, а не договорённостью.
- **NFR-002**: пароль публикатора отсутствует в логах, в argv на хосте и в
  отслеживаемых файлах сверх существующих dev-плейсхолдеров.
- **NFR-003**: тесты `test_stream_simulator.py` не требуют Docker и не
  пропускаются.
- **NFR-004**: сквозная проверка не использует `sleep` как средство
  синхронизации — только ожидание по дедлайну.

## 6. Сроки и контрольные точки

| Этап | Статус | Комментарий |
|---|---|---|
| 4.1 Точка входа | ✅ Выполнен | `scripts/simulate-stream-entrypoint.sh` |
| 4.2 Команды ffmpeg | ✅ Выполнен | Внутри точки входа |
| 4.3 Сервис compose | ✅ Выполнен | Профиль `simulator`, первый в проекте |
| 4.4 Негативный сценарий | ✅ Выполнен | Три проверки, все отвергнуты MediaMTX |
| 4.5 Обёртка | ✅ Выполнен | `scripts/simulate-stream.sh` |
| 4.6 Сквозная проверка | ✅ Выполнен | `scripts/e2e-stream-check.sh` + гард в `healthcheck.sh` |
| 4.7 Тесты | ✅ Выполнен | 15 тестов, без Docker, без пропусков |
| 4.8 Конфигурация и документация | ✅ Выполнен | `.env.example`, 5 документов, ADR-014 |

Легенда: ⬜ не начат · ⏳ в работе · ✅ выполнен · ⚠️ выполнен частично

## 7. Риски и ограничения

| Риск | Реальность | Митигация |
|---|---|---|
| Имитатор уезжает в production | Ansible вызывает `docker compose up -d --build` без `--profile`; в `.env.j2` нет пароля публикатора | Профиль + отсутствие переменной в prod + `test_production_deploy_does_not_enable_the_profile` + FAIL в `healthcheck.sh` |
| Публикация в необъявленный путь | Проверено: `400 Bad Request`, записи нет | Обёртка проверяет пути по `/v3/config/paths/list` заранее; то же поведение используется как негативная проверка №2 |
| В production объявлен только `cam1` | `mediamtx_paths` в ansible-роли | Многопоточный сценарий использует `cam1,live` — оба dev-only |
| Переполнение диска записями | ≈ 11 МБ/мин на поток при 1500k; `mediamtx-cleanup` работает раз в час | Сквозная проверка использует 640x480@15 500k; обёртка печатает оценку; `--cycles`/`--up` ограничивают reconnect; `stop` в `trap EXIT` |
| Права `1000:1000` на `recordings/` | На Windows bind-mount права не применяются; на Linux неверный владелец = MediaMTX молча ничего не пишет | Уже покрыто `healthcheck.sh`; §7 сквозной проверки превращает молчаливый отказ записи в FAIL |
| Утечка пароля в логи | ffmpeg требует пароль в URL (`-headers` только для HTTP) | `-loglevel error` закреплён тестом; в логах замаскированный URL; никогда в compose и argv обёртки; остаточная экспозиция в `ps` внутри контейнера — в ADR-014 |
| Нестабильность сквозной проверки | Первый ключевой кадр, первый файл fmp4, замер `bytesReceived` | `GOP_SECONDS=1`; каждое ожидание — `wait_until` с дедлайном |

## 8. Критерии завершения (Definition of Done)

- [x] Имитатор публикует поток одной командой, без ffmpeg на хосте.
- [x] Поддержаны все четыре сценария.
- [x] `scripts/e2e-stream-check.sh` автоматически подтверждает весь конвейер.
- [x] Имитатор не может стартовать без профиля и не может уехать в production.
- [x] Пароль публикатора отсутствует в логах и argv на хосте.
- [x] Обновлены `.env.example`, `docs/infrastructure.md`, `docs/testing.md`,
      `docs/E2E_CHECKLIST.md`, `docs/deployment.md`, `README.md`; добавлен
      ADR-014.
- [x] `sh -n` / `bash -n` для новых скриптов проходят.
- [x] `ruff check .`, `ruff format --check .`, `mypy app/ tests/`,
      `pytest tests/` завершаются успешно.

## 9. Результат прогона на живом стеке (2026-09-06)

Имитатор и все четыре сценария проверены на поднятом `docker compose`.

**Работает:**

- `start --paths cam1,live` — оба пути `ready`, по 2 дорожки (H264 + AAC),
  принятые байты растут независимо;
- `GET /api/streams` отдаёт оба потока, ключи в snake_case;
- `reconnect --up 5 --down 5 --cycles 2` — наблюдался переход
  `ready → not-ready → ready` и штатный выход после второго цикла;
- `negative` — MediaMTX отказал во всех трёх случаях (401 на неверный пароль,
  400 на необъявленный путь, 401 на `reader`), пароль в логах замаскирован;
- `stop` удаляет только контейнер имитатора, остальной стек не трогает;
- `docker compose config --services` без `--profile` имитатора не показывает.

**Сквозная проверка:** 18 OK, 0 WARN, 2 FAIL.

### Найденный дефект окружения: запись не ведётся

Оба FAIL — в §7 «Запись на диск», и вызваны не имитатором. В логах MediaMTX:

```
ERR [path cam1] [recorder] mkdir /recordings/cam1: permission denied
```

MediaMTX работает под `user: "1000:1000"`, а bind-mount `./recordings` виден
внутри контейнера как `drwxr-xr-x root root`. Поток принимается, отдаётся и
учитывается в Control API, но на диск не попадает ничего — то есть запись в
dev-окружении на Windows не работала и раньше, просто это некому было
заметить: до появления имитатора в стек ни разу не публиковали поток.

На Linux каталог создаёт Ansible с владельцем `1000:1000`, поэтому в
production дефект не воспроизводится; `scripts/healthcheck.sh` §10 это уже
проверяет. На Windows права bind-mount задаёт Docker Desktop, и `chown`
внутри контейнера не поможет.

Возможные направления (решение за владельцем проекта, в объём этого плана не
входит):

1. оставить как есть — запись проверяется только на Linux;
2. перевести `./recordings` в именованный том, но тогда файлы перестанут быть
   доступны с хоста напрямую, а `healthcheck.sh` §10 и архиватор потребуют
   пересмотра;
3. запускать `mediamtx` в dev-окружении под root — расходится с общей
   политикой non-root.

Сквозная проверка распознаёт эту ситуацию и печатает причину вместе с
командой `chown`, поэтому FAIL здесь диагностический, а не загадочный.

### Прочее

- `nginx` держал устаревший адрес upstream (`app` пересоздавался и сменил IP),
  из-за чего `/health` отвечал 502. Лечится `docker compose restart nginx`;
  §0 сквозной проверки теперь распознаёт 502 и подсказывает эту команду.
- `plan/PLAN_stage_5.md` не проходит `ruff format --check` — python-блок в
  markdown. Дефект существовал до этой работы и здесь не трогался.
