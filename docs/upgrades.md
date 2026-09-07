# Обновление версий модулей

Все версии в проекте закреплены точно: Python-пакеты — хешами в
`requirements.txt`, npm-пакеты — `package-lock.json`, образы — патч-тегами. Автоматических обновлений нет ни у одного
модуля, это осознанный размен воспроизводимости на ручную работу. Значит, обновление — отдельная задача, которую кто-то
делает по расписанию, а не побочный эффект пересборки.

Этот файл — про то, **как** обновлять. Про то, **нужно ли** (какие CVE открыты, что уже просрочено), — `docs/audit.md`.

---

## Карта модулей

| Модуль                    | Где закреплена версия                                                       | Чем обновляется                          |
|---------------------------|-----------------------------------------------------------------------------|------------------------------------------|
| Python-зависимости        | `pyproject.toml` (диапазоны) → `uv.lock` → `requirements.txt`               | `uv lock --upgrade` + `uv export`        |
| Python (интерпретатор)    | `Dockerfile` (две стадии), `pyproject.toml` (`requires-python`, ruff, mypy) | правка вручную                           |
| npm-зависимости фронтенда | `frontend/package.json` + `frontend/package-lock.json`                      | `npm update` / `npm install <pkg>@<ver>` |
| Node (сборка фронтенда)   | `nginx/Dockerfile`, стадия `frontend-builder`                               | правка вручную                           |
| Nginx                     | `nginx/Dockerfile`, runtime-стадия                                          | правка вручную                           |
| PostgreSQL                | `docker-compose.yml` → `postgres`                                           | правка тега (мажор — см. ниже)           |
| Redis                     | `docker-compose.yml` → `redis`                                              | правка тега                              |
| MinIO и `mc`              | `docker-compose.yml` → `minio`, `minio-init`                                | правка тега                              |
| MediaMTX                  | `docker-compose.yml` → `mediamtx` **и** `stream-simulator`                  | правка тега в двух местах                |
| pre-commit хуки           | `.pre-commit-config.yaml` (`rev`)                                           | `pre-commit autoupdate`                  |
| Ansible                   | `pyproject.toml`, dev-зависимость `ansible-core`                            | `uv lock --upgrade-package ansible-core` |
| Версия самого приложения  | шесть файлов, см. §8                                                        | правка вручную                           |

Больше версий нигде нет: `.env.example`, `scripts/*.sh`, роли Ansible и
`mediamtx/mediamtx.yml` не содержат ни одного тега — конфигурация от версий образов отвязана.

## Общие правила

1. **Одна группа за раз, отдельный коммит.** Обновили Python-пакеты — прогнали проверки — закоммитили. Потом образы.
   Коммит «обновил всё» не бисектится:
   когда через неделю сломается запись потоков, кандидатов будет двадцать.
2. **Никаких плавающих тегов.** `latest`, `postgres:18-alpine`, безверсионный
   `FROM` — запрещены. `postgres:18-alpine` не `latest`, но тоже двигается:
   пересборка через месяц принесёт другой патч-релиз без единого изменения в репозитории. Это стерегут
   `tests/test_compose_pinning.py` и
   `tests/test_dockerfile_pinning.py`.
3. **Lock-файлы правятся только инструментом.** `requirements.txt`
   перезаписывается `uv export`, `package-lock.json` — npm. Ручная правка разойдётся с источником при первой же
   генерации.
4. **Мажорные версии — отдельной задачей.** Патчи и миноры идут пачкой, мажорные (Postgres 18 → 19, React 19 → 20, Vite
   8 → 9) — по одному, с чтением changelog. Особо опасны те, что меняют данные на диске: формат каталога PostgreSQL,
   формат RDB у Redis.
5. **Перед обновлением production — бэкап базы.** Команды в
   `docs/deployment.md` → «Резервное копирование базы данных».

---

## 1. Python-зависимости backend

Полный разбор цепочки `pyproject.toml → uv.lock → requirements.txt` — в
`docs/uv.md`. Короткая последовательность:

```bash
uv lock --upgrade
uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file requirements.txt
uv sync --all-extras
```

Один пакет вместо всех — `uv lock --upgrade-package fastapi`.

Шаг `uv export` обязателен: образ собирается по `requirements.txt`, а не по
`pyproject.toml`. Забыли — локально новые версии, в контейнере старые.

Диапазоны в `pyproject.toml` трогают только тогда, когда нужна версия за их границей (то есть мажор). Внутри диапазонов
`uv lock --upgrade` справляется сам.

Проверка:

```bash
uv lock --check
ruff check app tests && mypy app tests
pytest tests/
uv run pip-audit -r requirements.txt
docker compose build app
```

Сборка идёт с `pip install --require-hashes`: несовпадение хеша или пакет, которого нет в лок-файле, роняют её — это и
есть защита от незаметного дрейфа.

## 2. Интерпретатор Python

Тег стоит в `Dockerfile` **дважды** — в стадии `builder` и в `runtime`. Обе должны быть одинаковыми: пакеты копируются
из первой во вторую по абсолютному пути.

При смене минорной версии (3.14 → 3.15) правятся ещё три места, иначе образ соберётся и упадёт на старте с
`ModuleNotFoundError`:

- `COPY --from=builder /usr/local/lib/python3.14/site-packages …` в `Dockerfile`
  — путь содержит минорную версию;
- `requires-python` в `pyproject.toml`;
- `target-version` у ruff и `python_version` у mypy.

Патч-версия (3.14.5 → 3.14.7) правится только в двух `FROM`.

```bash
docker manifest inspect python:3.14.7-slim > /dev/null && echo "тег существует"
docker compose build app && docker compose up -d app
docker compose logs -f app
```

## 3. Frontend

```bash
cd frontend
npm outdated          # Wanted — в рамках ограничений package.json, Latest — вообще
npm update            # обновляет в рамках ^
npm ci                # переустановить строго по обновлённому lock
npm run build         # tsc --noEmit + vite build
```

Мажорные версии `npm update` не тронет — для них
`npm install react@20 react-dom@20` и чтение release notes.

`package.json` и `package-lock.json` коммитятся **вместе**. Образ собирается через `npm ci`, который отказывается
работать при их расхождении; согласованность дополнительно проверяет
`test_frontend_lockfile_agrees_with_package_json`.

Если обновление пришло из `npm audit fix --force` — это мажоры с ломающими изменениями, их выносят в отдельный коммит с
прогоном сборки (`docs/audit.md`).

## 4. Базовые образы Node и Nginx

Оба в `nginx/Dockerfile`. Node участвует только в сборке фронтенда и в runtime не попадает, поэтому его обновление самое
безопасное: ошибка видна сразу на
`npm run build`.

```bash
docker manifest inspect node:22.23.4-alpine > /dev/null
docker compose build nginx && docker compose up -d nginx
curl -sf http://localhost/health
pytest tests/test_nginx_security.py
```

## 5. Сторонние образы в compose

Существование тега стоит проверить до правки:

```bash
docker manifest inspect postgres:18.7-alpine > /dev/null && echo ok
```

После правки:

```bash
docker compose pull <сервис>
docker compose up -d <сервис>
docker compose ps          # healthy у долгоживущих, Exit 0 у init-контейнеров
```

### PostgreSQL

Патч-версия (18.6 → 18.7) обновляется заменой тега: внутри мажора формат каталога данных не меняется, volume
`postgres-data` переиспользуется.

**Мажорная версия — не заменой тега.** Новый сервер откажется стартовать на каталоге, созданном предыдущим мажором (`database files are incompatible with
server`). Порядок: дамп на старой версии → остановка → пустой volume → восстановление на новой.

```bash
docker compose exec postgres pg_dump -U tacitus -Fc tacitus > pg_before_upgrade.dump
docker compose down
docker volume rm tacitus_postgres-data          # только после проверки дампа
# правим тег в docker-compose.yml
docker compose up -d postgres
docker compose exec -T postgres pg_restore -U tacitus -d tacitus < pg_before_upgrade.dump
docker compose up -d
```

Дамп храните, пока приложение не отработает на новой версии: удалённый volume не восстанавливается. Имя volume уточните
через `docker volume ls` — префикс задаётся именем проекта compose.

### Redis

Redis держит только счётчики rate limiting, потеря данных не критична — они набегают заново. Мажорное обновление всё же
лучше делать в тихое время: пустой Redis на старте означает сброшенные лимиты.

```bash
docker compose up -d redis
pytest tests/test_rate_limit.py
```

### MinIO и mc

Теги вида `RELEASE.2025-04-22T22-12-26Z` — это дата, а не semver. `minio` и
`minio/mc` обновляются вместе: клиент старше сервера обычно работает, обратное не гарантировано.

```bash
docker compose up -d minio minio-init
docker compose logs minio-init            # должен завершиться Exit 0
docker compose exec minio curl -sf http://localhost:9000/minio/health/live
pytest tests/test_s3_service.py
```

### MediaMTX

Тег указан **в двух сервисах** — `mediamtx` и `stream-simulator`. Симулятор намеренно использует тот же образ (в нём уже
есть ffmpeg): разъехавшиеся теги добавят стеку второй образ и вторую версию ffmpeg.

MediaMTX от версии к версии переименовывает ключи конфигурации и падает на старте при неизвестном ключе. После
обновления сверьте с changelog **обе**
конфигурации — `mediamtx/mediamtx.yml` (dev) и
`ansible/roles/mediamtx/templates/mediamtx.yml.j2` (production): они правятся раздельно и легко расходятся.

```bash
docker compose up -d mediamtx
docker compose logs mediamtx | head -30    # ошибки конфига видны сразу
pytest tests/test_mediamtx_security.py tests/test_stream_config.py
scripts/e2e-stream-check.sh                # публикация → путь → запись → архив
```

## 6. pre-commit хуки

```bash
uv run pre-commit autoupdate
uv run pre-commit run --all-files
```

`autoupdate` двигает `rev` у внешних репозиториев. Хук `mypy` объявлен как
`local` и берёт версию из окружения проекта (`pyproject.toml`) —
`autoupdate` его не трогает. Новый ruff почти всегда приносит новые правила, поэтому прогон по всем файлам обязателен:
иначе на них упадёт первый же посторонний коммит.

## 7. Ansible

`ansible-core` — обычная dev-зависимость:

```bash
uv lock --upgrade-package ansible-core
uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file requirements.txt
uv sync --all-extras
```

`requirements.txt` при этом не изменится — `--no-dev` отбрасывает dev-часть; экспорт делается для единообразия шага.

Проверять нужно на живом хосте: синтаксис плейбука проходит и на сломанной версии.

```bash
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbook.yml \
  -e target_host=<test-host> --check --diff
```

## 8. Версия приложения

`0.1.0` продублирована в шести местах и должна двигаться синхронно:

| Файл                            | Что править                                              |
|---------------------------------|----------------------------------------------------------|
| `pyproject.toml`                | `[project] version`                                      |
| `app/__init__.py`               | `__version__` (release для Sentry)                       |
| `frontend/package.json`         | `version`                                                |
| `docker-compose.yml`            | `tacitus-app:0.1.0` (три сервиса), `tacitus-nginx:0.1.0` |
| `ansible/group_vars/all.yml`    | `tacitus_version`                                        |
| `tests/test_compose_pinning.py` | константа `APP_IMAGE`                                    |

Расхождение `pyproject.toml` и `app/__init__.py` ловит
`tests/test_sentry.py::test_version_matches_pyproject`. Забытый тег в compose не ловит ничто: `docker compose up`
попытается спуллить несуществующий
`tacitus-app:0.2.0` из registry и упадёт. Упоминания образов в `docs/audit.md`
поправьте заодно.

---

## Проверка после обновления

Полный прогон, независимо от того, какой модуль обновлялся:

```bash
uv lock --check
ruff check app tests && ruff format --check app tests
mypy app tests
pytest tests/
uv run pip-audit -r requirements.txt
cd frontend && npm audit && cd ..
docker compose build
docker compose up -d
docker compose ps
curl -sf http://localhost/health
scripts/e2e-stream-check.sh
```

`pytest` без поднятой PostgreSQL молча пропускает всю логику auth и репозиториев — убедитесь, что в итоговой строке нет
`skipped`
(`docs/testing.md`).

После обновления базовых образов дополнительно:

```bash
docker scout cves tacitus-app:0.1.0
docker scout cves tacitus-nginx:0.1.0
```

Ручной сценарий, который автотесты не покрывают, — `docs/E2E_CHECKLIST.md`.

## Выкатка и откат

На сервер обновление уезжает обычным деплоем — он идемпотентен и пересобирает образы на хосте:

```bash
scripts/deploy.sh <host> --tags app
scripts/healthcheck.sh <host>
```

Откат — `git revert` коммита с обновлением и повторный деплой. Ради этого обновления и коммитятся по группам: откатить
«обновил MediaMTX» можно, откатить «обновил всё» — значит потерять и остальные обновления.

Необратимы ровно две вещи, и обе про данные, а не про код: мажорное обновление PostgreSQL (нужен дамп, см. §5) и
удалённый volume. Всё остальное откатывается пересборкой образа из предыдущего коммита.

## Регламент

| Периодичность | Что делать                                                                            |
|---------------|---------------------------------------------------------------------------------------|
| Раз в неделю  | `pip-audit`, `npm audit` — есть ли новые CVE (`docs/audit.md`)                        |
| Раз в месяц   | `uv lock --upgrade`, `npm outdated`, `pre-commit autoupdate` — патчи и миноры         |
| Раз в квартал | Ревизия тегов образов: вышли ли патчи PostgreSQL, Redis, Nginx, Node, MinIO, MediaMTX |
| По событию    | CVE на используемую версию — обновление вне очереди                                   |
| Перед релизом | Полный прогон из раздела «Проверка после обновления»                                  |

Мажорные обновления по расписанию не делаются — они планируются отдельно.
