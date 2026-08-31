# обновление до новейших версий:

## 1. Обновить все зависимости в lock-файле до последних совместимых версий

uv lock --upgrade

## 2. Синхронизировать окружение с новым lock-файлом

uv sync --all-extras

## 3. Проверить, что ничего не сломалось

ruff check . pytest tests/ -v

Полезные вариации

## Обновить только конкретный пакет

uv lock --upgrade-package fastapi

## Посмотреть, что устарело, без изменений

uv tree --outdated

Не забудьте про остальное

## pre-commit хуки (.pre-commit-config.yaml)

uv run pre-commit autoupdate

## Аудит уязвимостей (есть в dev-зависимостях)

uv run pip-audit
