# E2E Чеклист ручной проверки Tacitus

## 0. Предварительные условия

- [ ] Docker Desktop запущен (Linux containers).
- [ ] Порты `80` и `443` на хосте свободны, а также `${POSTGRES_HOST_PORT:-15432}`
      и `${TACITUS_MTX_RTSP_PORT:-8554}`.
- [ ] `.env` настроен (или используются dev-значения по умолчанию из `.env.example`).
- [ ] Установлены `curl` и `jq`.

> MinIO наружу **не публикуется** (ни `9000`, ни консоль `9001`) — доступ к
> хранилищу возможен только изнутри сети compose и по presigned URL.

## 1. Подъём стека

```bash
docker compose down -v
docker compose build --no-cache
docker compose up -d
```

- [ ] `docker compose ps` показывает 9 сервисов:
    - `Up`: `tacitus-app`, `tacitus-nginx`, `postgres`, `redis`, `minio`, `mediamtx`, `mediamtx-cleanup`
    - `Exit 0`: `minio-init`, `db-migrations`
- [ ] `docker compose logs db-migrations` содержит применение миграций до `a1c7e4d2b905`.

## 2. Health checks

- [ ] `curl -s http://localhost/health` → `{"status":"ok"}`
- [ ] `curl -s http://localhost/api/` → `{"message":"Hello World"}`
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

### 3.11 Brute-force блокировка

Шесть неудачных попыток подряд с одного IP:

```bash
for i in $(seq 1 6); do
  curl -s -o /dev/null -w "$i: %{http_code}\n" -X POST -H "Content-Type: application/json" \
    -d '{"username":"demo","password":"Wrong123!pass"}' http://localhost/auth/login
done
```

- [ ] Первые попытки → `401`, после превышения порога → `429`
- [ ] Подделка `-H "X-Forwarded-For: 9.9.9.9"` **не** сбрасывает счётчик
      (адрес берётся из записи nginx, а не из клиентского заголовка)

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
- [ ] Security-события (`auth.login.failed`, `file.upload`, …) видны в выводе
      контейнера; отдельный `security.log` появляется только при
      `TACITUS_LOG_DESTINATION=file` или `both`.
- [ ] Security headers присутствуют: `curl -sI http://localhost/ | grep -iE 'content-security-policy|strict-transport|x-frame-options'`
- [ ] `curl -s -o /dev/null -w '%{http_code}\n' http://localhost/.env` → `404`
- [ ] MinIO console недоступна снаружи: `curl -s -m 3 http://localhost:9001` → отказ в соединении

## 9. Остановка стека

```bash
docker compose down -v
```

- [ ] Все контейнеры остановлены.
- [ ] Volumes удалены (флаг `-v`): `minio-data`, `postgres-data`, `redis-data`,
      `app-logs`, `nginx-*`. **Данные пользователей будут потеряны** — на
      боевом хосте запускайте `docker compose down` без `-v`.

---

**Результат:** если все пункты отмечены — стек работоспособен end-to-end.
