# Управление зависимостями

## Два артефакта и роли между ними

| Файл | Чем является | Кто использует |
|------|--------------|----------------|
| `pyproject.toml` | Объявленные диапазоны (`>=`) | Источник намерения |
| `uv.lock` | Полное решение с точными версиями | `uv sync`, `uv run` — локальная разработка |
| `requirements.txt` | Экспорт runtime-части `uv.lock` с SHA-256 хешами | Сборка Docker-образа (`--require-hashes`), `pip-audit` |

`requirements.txt` **генерируется из `uv.lock`**, а не руками. Ранее он
собирался отдельной командой `pip-compile` и успел разойтись с
`pyproject.toml`: в нём не хватало `sqlalchemy`, `uvicorn`, `python-jose` и
`sentry-sdk`, то есть образ по нему было не собрать. Держите цепочку
`pyproject.toml → uv.lock → requirements.txt` в одном направлении.

## Обновление зависимостей

### 1. Обновить lock-файл

```bash
# все зависимости до последних совместимых версий
uv lock --upgrade

# либо только один пакет
uv lock --upgrade-package fastapi
```

### 2. Перегенерировать `requirements.txt`

**Обязательный шаг.** Без него Docker-образ соберётся со старыми версиями,
потому что сборка идёт по `requirements.txt`, а не по `pyproject.toml`.

```bash
uv export --frozen --no-dev --no-emit-project \
  --format requirements-txt --output-file requirements.txt
```

Флаги: `--frozen` — не трогать `uv.lock`; `--no-dev` — только runtime-часть,
без dev-инструментов; `--no-emit-project` — исключить сам пакет `tacitus`
(у локального пакета нет хеша). Хеши добавляются по умолчанию.

### 3. Синхронизировать локальное окружение

```bash
uv sync --all-extras
```

### 4. Проверить, что ничего не сломалось

```bash
ruff check app tests
mypy app tests
pytest tests/
```

### 5. Проверить уязвимости

```bash
uv run pip-audit -r requirements.txt
```

### 6. Пересобрать образ

```bash
docker compose build app
```

Сборка использует `pip install --require-hashes -r requirements.txt`:
несовпадение хеша или пакет, отсутствующий в лок-файле, роняют сборку — это
и есть защита от незаметного дрейфа версий.

## Полезные команды

```bash
# посмотреть, что устарело, без изменений
uv tree --outdated

# проверить, что uv.lock соответствует pyproject.toml
uv lock --check

# обновить pre-commit хуки
uv run pre-commit autoupdate
```

## Частые ошибки

- **Обновили `uv.lock`, забыли `uv export`.** Локально новые версии, в образе
  старые. Проверяется командой `uv lock --check` плюс пересборкой образа.
- **Добавили зависимость только в `pyproject.toml`.** Пока не выполнен
  `uv lock` и `uv export`, сборка образа упадёт на `--require-hashes`: пакета
  нет в лок-файле. Это ожидаемое поведение, а не поломка.
- **Правка `requirements.txt` вручную.** Файл перезаписывается экспортом;
  менять нужно `pyproject.toml`.
