# CLAUDE.md

Инструкции ниже перенесены из [AGENTS.md](AGENTS.md), чтобы Claude Code подхватывал их автоматически.

## Обязательные к прочтению файлы

- [.ai/system/context-rules.md](.ai/system/context-rules.md)
- [.ai/system/output-rules.md](.ai/system/output-rules.md)
- [.ai/system/platform-rules.md](.ai/system/platform-rules.md)
- [.ai/system/security-rules.md](.ai/system/security-rules.md)
- [.ai/project/coding-rules.md](.ai/project/coding-rules.md)
- [.ai/project/logging-rules.md](.ai/project/logging-rules.md)
- [.ai/project/review-checklist.md](.ai/project/review-checklist.md)
- [.ai/project/testing-rules.md](.ai/project/testing-rules.md)
- [.ai/project/tor-rules.md](.ai/project/tor-rules.md)
- [.ai/modules/fastapi.md](.ai/modules/fastapi.md)
- [.ai/modules/docker-compose.md](.ai/modules/docker-compose.md)

## Планирование

- Учитывай [plan_example/*.md](plan_example) как референс формата планирования.
- Исполняй [plan/PLAN.md](plan/PLAN.md).

## Техническое задание

- Техническое задание сервиса ведется в одном файле: [docs/ToR_<Project>_v1.0.md](docs/ToR_<Project>_v1.0.md).
- Файл описывает сервис так, как будто ТЗ было написано до разработки и сервис создан по нему.
- Любое изменение, затрагивающее требования (поведение, API, роли, модель данных, интеграции,
  переменные окружения, деплой), обязано обновлять ТЗ в том же коммите.
- Правила и структура файла: [.ai/project/tor-rules.md](.ai/project/tor-rules.md), рабочий порядок — навык `tor-sync`.
