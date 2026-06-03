# `.ai/` для разработчиков и DevOps

Этот каталог задает правила для локального AI-агента, который работает с кодом, конфигурацией, инфраструктурой и эксплуатационными задачами в закрытом контуре.

Основная цель `.ai/`:
- сократить риск небезопасных ответов и действий агента;
- задать единый порядок работы с кодом, prompt-ами и инфраструктурой;
- сделать ответы агента более предсказуемыми и пригодными для review.

Шаблон ориентирован на команды, которые работают с:
- Python;
- FastAPI, Django, Flask;
- PostgreSQL, Redis, Celery;
- Vault;
- Docker Compose, Kubernetes, Nginx, Apache;
- DevOps- и banking-сценариями.

## Содержание

- [Как читать этот каталог](#как-читать-этот-каталог)
- [Быстрый маршрут по ролям](#быстрый-маршрут-по-ролям)
- [Структура `.ai/`](#структура-ai)
- [Логика приоритета](#логика-приоритета)
- [Когда подключать модульные файлы](#когда-подключать-модульные-файлы)
- [Минимальные обязательные правила](#минимальные-обязательные-правила)
- [Практические рекомендации](#практические-рекомендации)

## Как читать этот каталог

Не нужно загружать весь `.ai/` в контекст целиком.

Рекомендуемый порядок чтения:
1. `AGENTS.md` в корне проекта, если он есть.
2. `.ai/README.md` как карта правил.
3. `.ai/system/*` как обязательные глобальные ограничения.
4. `.ai/project/*` как рабочие правила проекта.
5. `.ai/modules/*` только под конкретный стек или задачу.

Практический смысл такой:
- `system` отвечает за базовую безопасность и рамки поведения;
- `project` задает правила работы с кодом, тестами, логированием и prompt-ами;
- `modules` добавляет узкоспециализированные ограничения под конкретную технологию, включая стек-специфичные правила логирования.

## Быстрый маршрут по ролям

### Если вы разработчик

Начните с:
- `.ai/system/security-rules.md`
- `.ai/system/context-rules.md`
- `.ai/project/coding-rules.md`
- `.ai/project/testing-rules.md`
- `.ai/project/review-checklist.md`

Дальше подключайте технологические модули:
- `fastapi.md`, `django.md`, `flask.md`
- `postgres.md`, `redis.md`, `celery.md`
- `vault.md`

### Если вы DevOps / SRE / platform engineer

Начните с:
- `.ai/system/security-rules.md`
- `.ai/system/platform-rules.md`
- `.ai/system/output-rules.md`
- `.ai/project/infra-rules.md`
- `.ai/project/logging-rules.md`
- `.ai/project/secrets-rules.md`

Дальше подключайте инфраструктурные модули:
- `devops.md`
- `docker-compose.md`
- `kubernetes.md`
- `nginx-apache.md`
- `boto-yandex-cloud.md`
- `vault.md`

### Если задача смешанная

Для типовой backend-задачи с инфраструктурным следом обычно хватает такого набора:
- `.ai/system/security-rules.md`
- `.ai/system/context-rules.md`
- `.ai/project/coding-rules.md`
- `.ai/project/infra-rules.md`
- `.ai/project/testing-rules.md`
- нужный backend-модуль
- нужный infra-модуль

## Структура `.ai/`

```text
.ai/
  README.md
  system/
    platform-rules.md
    security-rules.md
    context-rules.md
    output-rules.md
  project/
    coding-rules.md
    database-rules.md
    infra-rules.md
    logging-rules.md
    prompt-dsl.md
    prompt-templates.md
    review-checklist.md
    secrets-rules.md
    testing-rules.md
  modules/
    banking.md
    boto-yandex-cloud.md
    celery.md
    devops.md
    django.md
    docker-compose.md
    fastapi.md
    flask.md
    kubernetes.md
    nginx-apache.md
    postgres.md
    redis.md
    vault.md
```

### `system/`

Это обязательный слой.

Файлы:
- `platform-rules.md` — общие рамки работы агента и ограничение действий;
- `security-rules.md` — базовые security-запреты;
- `context-rules.md` — что можно и нельзя передавать в контекст;
- `output-rules.md` — требования к итоговому ответу и high-risk output.

### `project/`

Это прикладной слой для повседневной работы в репозитории.

Файлы:
- `coding-rules.md` — правила генерации и изменения кода;
- `database-rules.md` — правила для SQL и работы с БД;
- `infra-rules.md` — правила для инфраструктурных изменений;
- `logging-rules.md` — правила логирования и трассировки;
- `prompt-dsl.md` — структура сложных запросов к агенту;
- `prompt-templates.md` — готовые шаблоны prompt-ов;
- `review-checklist.md` — короткий чек-лист проверки результата;
- `secrets-rules.md` — правила обращения с секретами;
- `testing-rules.md` — требования к тестам и проверке.

### `modules/`

Это точечные правила под стек или домен. Их подключают только по задаче.

Backend:
- `fastapi.md`
- `django.md`
- `flask.md`
- `celery.md`
- `postgres.md`
- `redis.md`

Infra:
- `devops.md`
- `docker-compose.md`
- `kubernetes.md`
- `nginx-apache.md`
- `boto-yandex-cloud.md`
- `vault.md`

Domain:
- `banking.md`

## Логика приоритета

При конфликте правил используйте такой порядок:
1. `AGENTS.md`
2. `.ai/system/*`
3. `.ai/project/*`
4. `.ai/modules/*`
5. локальный пользовательский запрос

Из этого следуют два практических правила:
- модульный файл не должен ослаблять ограничения из `system`;
- пользовательский запрос не должен обходить security-правила и правила работы с секретами.

## Когда подключать модульные файлы

Подключайте модуль только если он реально влияет на решение.

Примеры:
- есть FastAPI endpoint: подключайте `modules/fastapi.md`;
- есть Celery worker или async job: подключайте `modules/celery.md`;
- есть SQL, миграции, схема или индексы: подключайте `modules/postgres.md`;
- есть Kubernetes manifests: подключайте `modules/kubernetes.md`;
- есть Nginx/Apache config: подключайте `modules/nginx-apache.md`;
- есть секреты, Vault или cloud credentials flow: подключайте `modules/vault.md`;
- есть платежи, антифрод, подтверждения, audit trail: подключайте `modules/banking.md`.

Если задача не использует конкретный стек, модуль лучше не добавлять в контекст.

## Минимальные обязательные правила

Независимо от задачи:
- не передавать в модель реальные секреты, ключи, пароли, токены и сертификаты;
- не доверять output модели без review;
- не применять code, SQL, конфигурацию или инфраструктурные изменения без проверки;
- не выполнять destructive действия по умолчанию;
- не логировать чувствительные данные в явном виде;
- для критичных и production-сценариев требовать human review.

## Практические рекомендации

- Используйте `.ai/README.md` как навигацию, а не как источник всех правил.
- В prompt включайте только нужные файлы из `.ai/`.
- Для сложных задач сначала формализуйте запрос через `project/prompt-dsl.md`.
- Для повторяющихся задач опирайтесь на `project/prompt-templates.md`.
- Перед применением результата сверяйтесь с `project/review-checklist.md`.
- Для DevOps- и banking-изменений отдельно проверяйте rollback, audit trail и работу с секретами.
