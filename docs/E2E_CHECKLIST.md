# E2E Чеклист ручной проверки Tacitus

## 0. Предварительные условия

- [ ] Docker Desktop запущен (Linux containers).
- [ ] Порты `80` и `443` на хосте свободны, а также `${POSTGRES_HOST_PORT:-15432}`
      и `${TACITUS_MTX_RTSP_PORT:-8554}`.
- [ ] `.env` настроен (или используются dev-значения по умолчанию из `.env.example`).
- [ ] Установлены `curl` и `jq`.

> MinIO наружу **не публикуется** (ни `9000`, ни консоль `9001`) — доступ к
> хранилищу возможен только изнутри сети compose и по presigned URL.
>
> PostgreSQL публикуется **только на loopback** — `127.0.0.1` и `[::1]`.
> Проверить: `docker port tacitus-postgres` должен показать обе строки. Порт
> нужен тестам с хоста; отсутствие адреса IPv6 не ломает работу, но на Windows
> замедляет подключения по имени `localhost` примерно на 2 секунды каждое.

## 1. Подъём стека

```bash
docker compose down -v
docker compose build --no-cache
docker compose up -d
```

- [ ] `docker compose ps` показывает 9 сервисов:
    - `Up`: `tacitus-app`, `tacitus-nginx`, `postgres`, `redis`, `minio`, `mediamtx`, `mediamtx-cleanup`
    - `Exit 0`: `minio-init`, `db-migrations`
- [ ] `docker compose logs db-migrations` содержит применение миграций до `b2d8f6a3c714`.

## 2. Health checks

- [ ] `curl -s http://localhost/health` → `{"status":"ok"}`
- [ ] `docker compose exec postgres pg_isready -U tacitus` → `accepting connections`
- [ ] `docker compose exec minio curl -sf http://localhost:9000/minio/health/live` → `200`

## 3. Аутентификация (API)

Все auth-эндпоинты принимают **только JSON body**. Query-параметры вернут `422`.

Пароль должен удовлетворять политике: ≥ 8 символов, заглавная, строчная, цифра
и спецсимвол. Username: 3–64 символа из `a-zA-Z0-9._-`.

```bash
CREDS='{"username":"demo","password":"Demo123!pass"}'
```

### 3.1 Регистрация

```bash
curl -si -X POST -H "Content-Type: application/json" -d "$CREDS" \
  http://localhost/auth/register
```

- [ ] HTTP `201 Created`
- [ ] Тело: `{"username":"demo","message":"User registered successfully"}`

### 3.2 Повторная регистрация

```bash
curl -si -X POST -H "Content-Type: application/json" -d "$CREDS" \
  http://localhost/auth/register
```

- [ ] HTTP `400 Bad Request`
- [ ] Тело содержит `Username 'demo' already exists`

### 3.3 Слабый пароль и некорректный username

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H "Content-Type: application/json" \
  -d '{"username":"demo2","password":"demopass"}' http://localhost/auth/register
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H "Content-Type: application/json" \
  -d '{"username":"ab","password":"Demo123!pass"}' http://localhost/auth/register
```

- [ ] Оба запроса → HTTP `422`

### 3.4 Логин

```bash
curl -s -X POST -H "Content-Type: application/json" -d "$CREDS" \
  http://localhost/auth/login | jq
```

- [ ] HTTP `200 OK`
- [ ] Тело содержит `access_token`, `refresh_token` и `"token_type":"bearer"`

```bash
TOKENS=$(curl -s -X POST -H "Content-Type: application/json" -d "$CREDS" http://localhost/auth/login)
TOKEN=$(echo "$TOKENS" | jq -r '.access_token')
REFRESH=$(echo "$TOKENS" | jq -r '.refresh_token')
```

### 3.5 Логин с неверным паролем

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H "Content-Type: application/json" \
  -d '{"username":"demo","password":"Wrong123!pass"}' http://localhost/auth/login
```

- [ ] HTTP `401 Unauthorized`

### 3.6 Получение текущего пользователя

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost/auth/me
```

- [ ] HTTP `200 OK`, тело `{"username":"demo","message":null}`

### 3.7 Доступ без токена

```bash
curl -si http://localhost/auth/me
```

- [ ] HTTP `401 Unauthorized`
- [ ] Заголовок ответа содержит `WWW-Authenticate: Bearer`

### 3.8 Ротация refresh-токена

```bash
NEW=$(curl -s -X POST -H "Content-Type: application/json" \
  -d "{\"refresh_token\":\"$REFRESH\"}" http://localhost/auth/refresh)
echo "$NEW" | jq
```

- [ ] HTTP `200 OK`, выдана **новая пара** токенов
- [ ] `refresh_token` в ответе отличается от исходного

### 3.9 Повторное использование потраченного токена

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d "{\"refresh_token\":\"$REFRESH\"}" http://localhost/auth/refresh
```

- [ ] HTTP `401 Unauthorized`
- [ ] Тело содержит `Refresh token reuse detected`
- [ ] Новый refresh-токен из 3.8 тоже перестал работать (отозвана вся семья)
- [ ] В логах есть событие `auth.refresh.reuse_detected`

### 3.10 Logout

Получите свежую пару (повторите 3.4), затем:

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"refresh_token\":\"$REFRESH\"}" http://localhost/auth/logout
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOKEN" http://localhost/auth/me
```

- [ ] Logout → `200`, тело `{"message":"Logged out successfully"}`
- [ ] Последующий `/auth/me` с тем же access-токеном → `401` (jti в blacklist)

### 3.11 Rate limit и brute-force блокировка

Это **два независимых механизма**, и на `/auth/login` первым срабатывает
rate limit — 5 запросов в минуту с IP. Порог brute-force (5 попыток на пару
«пользователь + IP» за 15 минут) до срабатывания просто не доживает: оба
отвечают `429`, и по коду ответа их не различить — только по телу.

```bash
for i in $(seq 1 6); do
  curl -s -w "$i: %{http_code} " -X POST -H "Content-Type: application/json" \
    -d '{"username":"demo","password":"Wrong123!pass"}' http://localhost/auth/login \
    | jq -r '.error // .detail'
done
```

- [ ] Первые попытки → `401` с `Incorrect username or password`
- [ ] Шестая → `429` с `Rate limit exceeded` — это лимитер, не brute-force
- [ ] Подделка `-H "X-Forwarded-For: 9.9.9.9"` **не** сбрасывает счётчик
      (адрес берётся из записи nginx, а не из клиентского заголовка)

Чтобы дойти именно до brute-force, сбрасывайте счётчики лимитера между
попытками. В этом проекте Redis не хранит ничего, кроме них:

```bash
for i in $(seq 1 6); do
  docker exec tacitus-redis redis-cli FLUSHDB > /dev/null
  curl -s -w "$i: %{http_code} " -X POST -H "Content-Type: application/json" \
    -d '{"username":"demo","password":"Wrong123!pass"}' http://localhost/auth/login \
    | jq -r '.error // .detail'
done
```

- [ ] Шестая попытка → `429` с сообщением о временной блокировке аккаунта
- [ ] Попытки записались:

```bash
docker exec tacitus-postgres psql -U tacitus -d tacitus \
  -c "SELECT username, ip_address, created_at FROM failed_login_attempts;"
```

> Записи должны быть на месте, **хотя каждый запрос завершался 401**. Пустая
> таблица означает, что сломана durability записи неудачных входов и
> brute-force защита фактически выключена (см. `docs/backend.md` → «Границы
> транзакций»).

### 3.12 Границы длины пароля

```bash
LONG=$(python -c "print('Aa1!' + 'x' * 5000)")
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H "Content-Type: application/json" \
  -d "{\"username\":\"demo\",\"password\":\"$LONG\"}" http://localhost/auth/login
```

- [ ] `422`, а **не** `500`: passlib отказывается хешировать строку длиннее
      4096 символов, и это должно отсекаться схемой, а не всплывать
      необработанным исключением

### 3.13 Пароль не возвращается в ответе

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"username":"demo2","password":"short"}' http://localhost/auth/register
```

- [ ] `422`, в теле присутствуют `loc` и `msg`
- [ ] В теле **нет** поля `input` и **нет** самой строки `short`

### 3.14 Несуществующий аккаунт неотличим по времени ответа

```bash
docker exec tacitus-redis redis-cli FLUSHDB > /dev/null
curl -s -o /dev/null -w 'существующий, неверный пароль: %{time_total}s\n' \
  -X POST -H "Content-Type: application/json" \
  -d '{"username":"demo","password":"Wrong123!pass"}' http://localhost/auth/login

docker exec tacitus-redis redis-cli FLUSHDB > /dev/null
curl -s -o /dev/null -w 'несуществующий аккаунт:        %{time_total}s\n' \
  -X POST -H "Content-Type: application/json" \
  -d '{"username":"no-such-user","password":"Wrong123!pass"}' http://localhost/auth/login
```

- [ ] Оба ответа — `401` с одинаковым телом
- [ ] Времена сопоставимы. Разница в разы означала бы, что имена пользователей
      можно перебирать по скорости ответа, ни разу не угадав пароль

## 4. Защита файловых эндпоинтов

Получите свежий токен (3.4) в `$TOKEN`.

### 4.1 Без токена

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://localhost/api/files/
```

- [ ] HTTP `401 Unauthorized`

### 4.2 С невалидным токеном

```bash
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer invalid" http://localhost/api/files/
```

- [ ] HTTP `401 Unauthorized`

### 4.3 С валидным токеном

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost/api/files/
```

- [ ] HTTP `200 OK`, тело `{"files":[]}` (или список файлов)

## 5. CRUD файлов (через API)

Загрузка проверяет MIME-тип, расширение и их соответствие друг другу, поэтому
тип нужно указать явно.

### 5.1 Загрузка файла

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -F "file=@README.md;type=text/markdown" \
  http://localhost/api/files/upload
```

- [ ] HTTP `200 OK`
- [ ] Тело содержит `"filename":"README.md"`

### 5.2 Отклонение запрещённого типа

```bash
printf '#!/bin/sh\necho hi\n' > /tmp/evil.sh
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H "Authorization: Bearer $TOKEN" \
  -F "file=@/tmp/evil.sh;type=text/plain" http://localhost/api/files/upload
```

- [ ] HTTP `400` или `415` (расширение запрещено / не совпадает с MIME)

### 5.3 Список файлов

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost/api/files/
```

- [ ] Тело содержит `"README.md"` в массиве `files`

### 5.4 Статистика bucket

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost/api/files/stats | jq
```

- [ ] HTTP `200 OK`
- [ ] Поля `total_files`, `total_size_bytes`, `total_size_human`, `largest_file`

### 5.5 Получение presigned URL

```bash
URL=$(curl -s -H "Authorization: Bearer $TOKEN" http://localhost/api/files/README.md | jq -r '.url')
curl -s -o /dev/null -w '%{http_code}\n' "$URL"
```

- [ ] HTTP `200 OK`, тело содержит `url`
- [ ] Переход по ссылке отдаёт файл (HTTP `200` + содержимое)
- [ ] Несуществующий файл (`/api/files/nope.txt`) → `404`

### 5.6 Удаление файла

```bash
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" http://localhost/api/files/README.md
```

- [ ] HTTP `200 OK`
- [ ] Повторный `GET /api/files/` не содержит `README.md`

## 6. RTSP-потоки

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost/api/streams | jq
```

- [ ] HTTP `200 OK`, поля `page_count`, `item_count`, `items`
- [ ] В `items[]` поля `conf_name`, `ready_time`, `bytes_received`, `bytes_sent`
      (snake_case; camelCase-имена MediaMTX наружу не отдаются)
- [ ] При остановленном MediaMTX (`docker compose stop mediamtx`) → `503`
- [ ] Запрос несуществующего пути `/api/streams/nope` → `404`
- [ ] Более 60 запросов в минуту → `429`

### 6.1 Симуляция публикации

Без источника все проверки выше видят пустой список. Имитатор публикует
синтетический поток; настоящая камера не нужна. Подробности — в
`docs/testing.md`.

```bash
scripts/simulate-stream.sh start --paths cam1,live
scripts/simulate-stream.sh status
```

- [ ] `start` завершился с кодом 0 (значит, MediaMTX сообщил `ready`)
- [ ] `status` показывает `true` в колонке READY и `2` дорожки по обоим путям
- [ ] Принятые байты растут между двумя замерами
- [ ] `curl -s -H "Authorization: Bearer $TOKEN" http://localhost/api/streams | jq`
      теперь содержит `cam1` и `live`
- [ ] В `recordings/cam1/` появились файлы `.mp4`
      (если нет — см. «Если записи не появляются» в `docs/testing.md`)
- [ ] `scripts/simulate-stream.sh negative` → код 0: MediaMTX отказал при
      неверном пароле, необъявленном пути и публикации от имени `reader`
- [ ] `scripts/simulate-stream.sh stop` удаляет только контейнер имитатора,
      остальной стек продолжает работать
- [ ] `docker compose ps` **без** `--profile` не показывает
      `tacitus-stream-simulator`

Полностью автоматически то же самое проверяет `## 13`.

## 7. React frontend (браузер)

- [ ] Открыть `http://localhost/` в браузере.
- [ ] Отображается страница логина (форма username/password).
- [ ] Ввести `demo` / `Demo123!pass` → успешный вход, переход к файловому менеджеру.
- [ ] Отображается список файлов (соответствует `GET /api/files/`).
- [ ] Загрузка нового файла через UI → файл появляется в списке.
- [ ] Нажатие "Download" → открывается presigned URL, файл скачивается.
- [ ] Нажатие "Delete" → файл исчезает из списка.
- [ ] Обновление страницы (F5) → токен восстанавливается из `localStorage`, пользователь остаётся авторизован.
- [ ] По истечении 15 минут access-токен обновляется через `/auth/refresh` без разлогина.
- [ ] Нажатие "Logout" → токены удаляются, возврат на страницу логина.

## 8. Безопасность и логи

- [ ] В логах контейнера `tacitus-app` (`docker compose logs app`) **отсутствуют**:
    - JWT-токены и refresh-токены
    - Пароли в plaintext
    - S3 secret keys
- [ ] В логах присутствуют: `request_id`, маршрут (`route`), HTTP-статус, имя пользователя.
- [ ] В строках JSON-лога **нет** поля `taskName`.
- [ ] Security-события (`auth.login.failed`, `file.upload`, …) записываются.
      **Куда именно — зависит от `TACITUS_LOG_DESTINATION`:** при `stdout` они
      идут в вывод контейнера, а при `file` или `both` (значение в `.env` по
      умолчанию) — **только** в отдельный `/var/log/tacitus/security.log`, минуя
      stdout. То есть `docker compose logs app` их не покажет:

```bash
docker exec tacitus-app sh -c 'tail -5 /var/log/tacitus/security.log'
```

- [ ] `logger.exception` пишет traceback, а не только сообщение — в JSON-логе
      у записей уровня `ERROR` должно быть поле `exception`.
- [ ] Ответ `413` (тело больше лимита) содержит `request_id` и заголовок
      `x-request-id` — без них клиенту нечего процитировать в баг-репорте.
- [ ] Security headers присутствуют: `curl -sI http://localhost/ | grep -iE 'content-security-policy|strict-transport|x-frame-options'`
- [ ] `curl -s -o /dev/null -w '%{http_code}\n' http://localhost/.env` → `404`
- [ ] MinIO console недоступна снаружи: `curl -s -m 3 http://localhost:9001` → отказ в соединении
- [ ] `GET /api/` → `404`. Демо-эндпоинт «Hello World» удалён; liveness-проверка
      деплоя выполняется по `/health`.
- [ ] `/hls/` и `/webrtc/` уходят в SPA-fallback (`200` с `index.html`), а не
      проксируются в MediaMTX. Отдельных локаций для них нет: соответствующие
      протоколы в `mediamtx.yml` выключены, и проксирование было мёртвым.

## 9. Устойчивость

### 9.1 Перезапуск базы данных

```bash
docker exec tacitus-redis redis-cli FLUSHDB > /dev/null
curl -s -o /dev/null -H "Authorization: Bearer $TOKEN" http://localhost/api/files/
docker restart tacitus-postgres && sleep 12
curl -s -o /dev/null -w 'первый запрос после рестарта: %{http_code}\n' \
  -H "Authorization: Bearer $TOKEN" http://localhost/api/files/
```

- [ ] `200`, а **не** `500`

> Пул соединений должен молча заменить соединения к погибшему процессу
> (`pool_pre_ping`). Если здесь `500` — приложение будет отдавать ошибки после
> каждого обслуживания БД. См. `docs/backend.md` → «Пул соединений».

### 9.2 Атомарность выхода из системы

```bash
docker exec tacitus-redis redis-cli FLUSHDB > /dev/null
TOKENS=$(curl -s -X POST -H "Content-Type: application/json" -d "$CREDS" http://localhost/auth/login)
T=$(echo "$TOKENS" | jq -r '.access_token'); R=$(echo "$TOKENS" | jq -r '.refresh_token')

curl -s -o /dev/null -X POST -H "Authorization: Bearer $T" -H "Content-Type: application/json" \
  -d "{\"refresh_token\":\"$R\"}" http://localhost/auth/logout

curl -s -o /dev/null -w 'access:  %{http_code}\n' -H "Authorization: Bearer $T" http://localhost/auth/me
curl -s -o /dev/null -w 'refresh: %{http_code}\n' -X POST -H "Content-Type: application/json" \
  -d "{\"refresh_token\":\"$R\"}" http://localhost/auth/refresh
```

- [ ] Оба `401`

> Отзыв обоих токенов идёт одной транзакцией. Если refresh остался живым,
> выход только выглядит выполненным: держатель токена продолжит выпускать
> новые access-токены.

### 9.3 Чужой refresh-токен не отзывается

Заведите второго пользователя (см. §11), войдите обоими и попробуйте
разлогинить первого токеном второго:

```bash
curl -s -o /dev/null -X POST -H "Authorization: Bearer $TOKEN_MALLORY" \
  -H "Content-Type: application/json" \
  -d "{\"refresh_token\":\"$REFRESH_ALICE\"}" http://localhost/auth/logout

curl -s -o /dev/null -w 'сессия Alice: %{http_code}\n' -X POST \
  -H "Content-Type: application/json" \
  -d "{\"refresh_token\":\"$REFRESH_ALICE\"}" http://localhost/auth/refresh
```

- [ ] `200` — сессия Alice не тронута
- [ ] В `security.log` появилось событие `auth.logout.foreign_refresh_token`

### 9.4 Валидация `X-Request-Id`

```bash
curl -sI -H "X-Request-Id: trace-abc123" http://localhost/health | grep -i x-request-id
curl -sI -H "X-Request-Id: $(python -c "print('A'*200)")" http://localhost/health | grep -i x-request-id
curl -sI -H "X-Request-Id: id with spaces" http://localhost/health | grep -i x-request-id
```

- [ ] Корректный `trace-abc123` возвращается как есть
- [ ] Значение из 200 символов заменено на UUID (36 символов)
- [ ] Значение с пробелами заменено на UUID

## 10. Аудит зависимостей

Полная процедура — [`docs/audit.md`](audit.md). Минимум перед релизом:

```bash
pip-audit -r requirements.txt
cd frontend && npm audit
```

- [ ] Backend: только известная и принятая уязвимость `ecdsa` (PYSEC-2026-1325)
- [ ] Frontend: `found 0 vulnerabilities`

## 11. Заведение пользователя вручную

Нужно, когда исчерпан лимит регистраций (3 в час с IP), требуется засеять
данные скриптом или восстановить доступ.

### 11.1 Через API — обычный путь

```bash
curl -X POST -H "Content-Type: application/json" \
  -d '{"username":"ivan","password":"Str0ng!Pass"}' \
  http://localhost/auth/register
```

Требования: имя 3–64 символа из `a-zA-Z0-9._-`; пароль 8–128 символов,
обязательно заглавная, строчная, цифра и спецсимвол.

Упёрлись в `429` — сбросьте счётчики лимитера (dev-окружение, Redis хранит
только их; на боевом хосте так делать не надо, там дождитесь окончания часа):

```bash
docker exec tacitus-redis redis-cli FLUSHDB
```

### 11.2 Через сервис внутри контейнера

Лимит здесь не применяется — он живёт на уровне HTTP. Валидация и хеширование
те же самые.

```bash
docker exec tacitus-app python -c "
import asyncio
from app.db.engine import AsyncSessionLocal
from app.services.auth_service import register_user
async def main():
    async with AsyncSessionLocal() as s:
        print(await register_user(s, 'ivan', 'Str0ng!Pass', '127.0.0.1'))
asyncio.run(main())"
```

### 11.3 Прямым SQL

Пароль нужно захешировать заранее: колонка `password_hash` хранит
`pbkdf2_sha256`, и вписанный открытым текстом пароль даст пользователя,
который не сможет войти.

```bash
HASH=$(docker exec tacitus-app python -c \
  "from app.services.auth_service import hash_password; print(hash_password('Str0ng!Pass'))")

docker exec tacitus-postgres psql -U tacitus -d tacitus -c \
  "INSERT INTO users (id, username, password_hash, is_active)
   VALUES (gen_random_uuid(), 'ivan', '$HASH', true);"
```

> Этот путь **обходит проверку политики паролей** — база примет и `12345`.
> Годится для отладки и восстановления доступа, для регулярного заведения
> людей — нет.

### 11.4 Отключение и удаление

Ролей в проекте нет: в таблице `users` только `id`, `username`,
`password_hash`, `is_active`, `created_at`, `updated_at`. Все пользователи
равноправны, администратора завести нечем.

```bash
# отключить, не удаляя
docker exec tacitus-postgres psql -U tacitus -d tacitus -c \
  "UPDATE users SET is_active = false WHERE username = 'ivan';"

# сразу оборвать активные сессии
docker exec tacitus-postgres psql -U tacitus -d tacitus -c \
  "UPDATE refresh_tokens SET revoked_at = now()
   WHERE user_id = (SELECT id FROM users WHERE username = 'ivan')
     AND revoked_at IS NULL;"

# удалить (refresh-токены уйдут каскадом)
docker exec tacitus-postgres psql -U tacitus -d tacitus -c \
  "DELETE FROM users WHERE username = 'ivan';"
```

- [ ] После отключения вход даёт `401` — за то же время, что и неверный пароль
- [ ] Выданные ранее access-токены живут ещё до 15 минут; refresh-токены сами
      не отзываются, поэтому второй командой их гасят явно

## 12. Остановка стека

```bash
docker compose down -v
```

- [ ] Все контейнеры остановлены.
- [ ] Volumes удалены (флаг `-v`): `minio-data`, `postgres-data`, `redis-data`,
      `app-logs`, `nginx-*`. **Данные пользователей будут потеряны** — на
      боевом хосте запускайте `docker compose down` без `-v`.

---

**Результат:** если все пункты отмечены — стек работоспособен end-to-end.

## 13. Автоматическая E2E-проверка потока

Всё из §6.1 одним скриптом, с отчётом и кодом выхода.

```bash
scripts/e2e-stream-check.sh
```

- [ ] Итоговая строка показывает `FAIL: 0`
- [ ] Код выхода 0
- [ ] После завершения `docker compose ps` не содержит
      `tacitus-stream-simulator` (скрипт убирает имитатор через `trap EXIT`)

Код выхода 2 означает, что проверку **не удалось выполнить** (стек не поднят,
нет `curl`, не задан `TACITUS_SIM_PUBLISH_PASSWORD`), а не что всё хорошо.

Проверка архивации выполняется отдельно и только в dev — она безвозвратно
переносит локальные записи в S3:

```bash
scripts/e2e-stream-check.sh --with-archive
```
