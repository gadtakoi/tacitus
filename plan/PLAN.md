# План: документирование и усиление аутентификации при скачивании файлов из S3/MinIO

## 1. Цель

Зафиксировать, описать и при необходимости усилить модель аутентификации, которая используется при скачивании файлов из
хранилища S3/MinIO. Также обеспечить подробные комментарии/docstrings для нового или изменённого кода и актуализировать
пользовательскую документацию.

> **Важно:** этот план не объединяется с предыдущими `PLAN_stage_*.md`. Он
> описывает только задачи, связанные с аутентификацией скачивания файлов,
> код-документацией и сопроводительной документацией.

## 2. Текущее состояние (аудит)

Скачивание файлов реализовано через endpoint `GET /files/{filename}` в
`app/api/routers/files.py`:

1. Клиент отправляет запрос с JWT Bearer токеном.
2. `get_current_user` проверяет подпись JWT и сверяет `jti` с таблицей
   `revoked_tokens` в PostgreSQL.
3. Имя файла санитизируется функцией `sanitize_filename()`.
4. `S3Service.get_presigned_url()` генерирует временную подписанную ссылку (presigned URL) через boto3 для метода
   `get_object`.
5. Клиент получает JSON с полем `url` и скачивает файл напрямую из MinIO/S3.

Текущие параметры:

- Время жизни presigned URL: **3600 секунд** (default).
- Доступ к presigned URL: любой, у кого есть ссылка, пока она не истекла.
- Аутентификация на уровне API: JWT Bearer + revocation check.
- Rate limiting на `GET /files/{filename}`: **не настроен**.
- Security-событие `file.download`: **не логируется**.
- Проверка существования объекта перед выдачей URL: **не выполняется**.

## 3. Пункты плана

### 3.1. Проектирование и фиксация модели аутентификации

- [x] Сформулировать окончательную модель аутентификации для скачивания файлов:
    - JWT Bearer token защищает endpoint `/files/{filename}`.
    - Presigned URL выдаётся только аутентифицированному пользователю.
    - Presigned URL является краткосрочным и подписанным S3 credentials приложения, что делает его непредсказуемым и
      ограниченным по времени.
    - Доступ по presigned URL не требует дополнительных credentials клиента.
- [x] Определить, нужны ли дополнительные ограничения:
    - сократить TTL presigned URL (рекомендуется 300–600 секунд);
    - добавить rate limit на `GET /files/{filename}`;
    - добавить security event `file.download`;
    - проверять существование файла в bucket перед генерацией URL.
- [x] Если модель изменяется, добавить/обновить ADR в `plan/DECISIONS.md`
  (например, ADR-011 "S3 download authentication model").

### 3.2. Усиление защиты presigned URL

- [x] Добавить настройку `TACITUS_PRESIGNED_URL_EXPIRATION` в
  `app/core/config.py` с разумным default (например, 300 секунд).
- [x] Обновить `S3Service.get_presigned_url()`:
    - использовать `settings.presigned_url_expiration` вместо жёстко заданных 3600 секунд;
    - (опционально) проверить существование объекта через `head_object` перед генерацией URL и возвращать 404, если файл
      не найден.
- [x] Добавить декоратор `@limiter.limit(...)` на `GET /files/{filename}` в
  `app/api/routers/files.py` (например, 30/минуту на пользователя).
- [x] Добавить логирование security-события `file.download` в
  `app/core/security_logging.py` (если отсутствует) и вызвать его в endpoint.

### 3.3. Подробные комментарии и docstrings

- [x] Добавить/обновить docstring у `S3Service.get_presigned_url()`, объясняя:
    - что такое presigned URL;
    - как он аутентифицируется (подпись S3 credentials приложения);
    - почему ссылка безопасна (ограниченный срок действия, непредсказуемость);
    - ответственность вызывающего кода за проверку JWT до вызова.
- [x] Добавить/обновить docstring у `get_file_url` в
  `app/api/routers/files.py`, описывая полный flow аутентификации и выдачи URL.
- [x] Добавить inline-комментарии в `get_file_url` для ключевых шагов:
    - валидация JWT;
    - санитизация имени файла;
    - генерация presigned URL;
    - аудит через security logger.
- [x] Добавить docstring/комментарий для новой настройки
  `presigned_url_expiration` в `app/core/config.py`.
- [x] Убедиться, что все новые и изменённые функции имеют docstrings в формате Google/NumPy (как в остальном проекте).

### 3.4. Обновление документации безопасности

- [x] Добавить в `docs/security.md` раздел **"S3/MinIO Download Authentication"**:
    - диаграмма или пошаговое описание flow: JWT API → presigned URL → S3;
    - кто авторизован (любой пользователь с валидным access token);
    - срок действия presigned URL и способ его конфигурации;
    - объяснение, почему presigned URL сам по себе является механизмом авторизации (подписан временными credentials
      приложения);
    - рекомендации по production: bucket policy должна запрещать публичный доступ, MinIO console не должен быть exposed,
      presigned URL должен быть короткоживущим.
- [x] Обновить таблицу rate limits в `docs/security.md`, добавив строку для
  `GET /files/{filename}`.
- [x] Обновить таблицу security events в `docs/security.md`, добавив
  `file.download`.

### 3.5. Обновление документации бэкенда

- [x] Добавить в `docs/backend.md` подраздел **"Скачивание файлов"**:
    - endpoint `GET /files/{filename}`;
    - требование JWT Bearer;
    - формат ответа `FileUrlResponse`;
    - порядок взаимодействия клиента с MinIO/S3 по полученной ссылке;
    - переменная окружения `TACITUS_PRESIGNED_URL_EXPIRATION`.
- [x] При необходимости обновить общую таблицу endpoints в `docs/backend.md`.

### 3.6. Обновление/добавление тестов

- [x] Обновить `tests/test_files_router.py`:
    - проверить, что `GET /files/{filename}` без токена возвращает 401;
    - проверить, что отозванный токен возвращает 401;
    - проверить, что в ответе присутствует корректный presigned URL;
    - проверить, что TTL presigned URL соответствует настройке (mock).
- [x] Добавить/обновить тесты rate limit для `GET /files/{filename}`.
- [x] Добавить тест, что `file.download` логируется в security log.
- [x] Обновить `tests/test_s3_service.py` при изменении сигнатуры
  `get_presigned_url` или добавлении `head_object`.

### 3.7. Финальная проверка

- [x] Запустить `ruff check .` и исправить замечания.
- [x] Запустить `ruff format --check .` и при необходимости отформатировать.
- [x] Запустить `mypy app/ tests/` и исправить ошибки типизации.
- [x] Запустить `pytest tests/` и убедиться, что все независимые от БД тесты проходят, а БД-зависимые корректно
  пропускаются.
- [x] Проверить консистентность между `docs/security.md`, `docs/backend.md`, кодом и тестами.
- [x] Закоммитить изменения с понятным сообщением (например,
  `docs+security: document and harden S3 download auth`).

## 4. Критерии приёмки

- [x] В `docs/security.md` появился раздел, явно описывающий модель аутентификации при скачивании файлов из S3/MinIO.
- [x] В `docs/backend.md` описан endpoint `/files/{filename}` и полный flow получения presigned URL.
- [x] Код `app/api/routers/files.py`, `app/services/s3_service.py` и
  `app/core/config.py` содержит подробные docstrings и комментарии для всех новых/изменённых участков.
- [x] Добавлены или обновлены тесты, покрывающие аутентификацию, rate limit и logging скачивания.
- [x] `ruff check .`, `ruff format --check .`, `mypy app/ tests/` и
  `pytest tests/` завершаются успешно.
- [x] (Если менялась модель) в `plan/DECISIONS.md` добавлен соответствующий ADR.
