# Security

## Dependency & Supply Chain Security

### Dependency Locking

Проект использует **pip-tools** для воспроизводимой установки зависимостей.

- Исходные зависимости описаны в `pyproject.toml`.
- Зафиксированные зависимости с SHA-256 хешами хранятся в `requirements.txt`.
- Обновление lock-файла:

```bash
# Установить pip-tools
pip install pip-tools

# Перегенерировать requirements.txt с хешами
pip-compile --generate-hashes pyproject.toml -o requirements.txt
```

### Dependency Scanning

#### Python — pip-audit

В `pyproject.toml` добавлен `pip-audit` в dev-зависимости. В `pre-commit` настроен hook, который сканирует `requirements.txt` на наличие известных уязвимостей:

```bash
# Ручной запуск
python -m pip_audit -r requirements.txt
```

#### Frontend — npm audit

В `.pre-commit-config.yaml` добавлен hook для `npm audit`. Frontend зависимости сканируются при каждом коммите:

```bash
cd frontend
npm audit --audit-level=moderate
```

### SBOM (Software Bill of Materials)

Для генерации SBOM используются:

- **Python:** `cyclonedx-bom` (CycloneDX)
- **Frontend:** `@cyclonedx/cyclonedx-npm`

Единый скрипт генерации:

```bash
python scripts/generate-sbom.py
```

Результат:

- `sbom-python.json` — SBOM для Python-зависимостей
- `frontend/sbom.json` — SBOM для frontend-зависимостей

Артефакты SBOM не коммитятся в репозиторий. Они генерируются при подготовке релиза и прикрепляются к нему.
