# E2E Чеклист ручной проверки Tacitus

## 0. Предварительные условия

- [ ] Docker Desktop запущен (Linux containers).
- [ ] Порты `80`, `9000`, `9001` на хосте свободны.
- [ ] `.env` настроен (или используются dev-значения по умолчанию).

## 1. Подъём стека

```bash
docker compose down -v
docker compose build --no-cache
docker compose up -d
```

- [ ] Все 4 сервиса в статусе `Up` (`docker compose ps`):
  - `tacitus-app`
  - `tacitus-nginx`
  - `tacitus-minio`
  - `tacitus-minio-init` (завершился с `Exit 0`)

## 2. Health checks

- [ ] `curl -s http://localhost/health` → `{"status":"ok"}`
- [ ] `curl -s http://localhost/` → `{"message":"Hello World"}`
- [ ] `curl -s http://localhost:9000/minio/health/live` → `200 OK`

## 3. Аутентификация (API)

### 3.1 Регистрация
```bash
curl -s -X POST "http://localhost/auth/register?username=demo&password=demopass"
```
- [ ] HTTP `201 Created`
- [ ] Тело: `{"username":"demo","message":"User registered successfully"}`

### 3.2 Повторная регистрация
```bash
curl -s -X POST "http://localhost/auth/register?username=demo&password=demopass"
```
- [ ] HTTP `400 Bad Request`
- [ ] Тело содержит `"Username already registered"`

### 3.3 Логин
```bash
curl -s -X POST "http://localhost/auth/login?username=demo&password=demopass"
```
- [ ] HTTP `200 OK`
- [ ] Тело содержит `"access_token"` и `"token_type":"bearer"`

### 3.4 Логин с неверным паролем
```bash
curl -s -X POST "http://localhost/auth/login?username=demo&password=wrong"
```
- [ ] HTTP `401 Unauthorized`

### 3.5 Получение текущего пользователя
```bash
TOKEN=$(curl -s -X POST "http://localhost/auth/login?username=demo&password=demopass" | jq -r '.access_token')
curl -s -H "Authorization: Bearer $TOKEN" http://localhost/auth/me
```
- [ ] HTTP `200 OK`
- [ ] Тело: `{"username":"demo"}`

### 3.6 Доступ без токена
```bash
curl -s http://localhost/auth/me
```
- [ ] HTTP `401 Unauthorized`
- [ ] Заголовок ответа содержит `WWW-Authenticate: Bearer`

## 4. Защита файловых эндпоинтов

### 4.1 Без токена
```bash
curl -s http://localhost/files/
```
- [ ] HTTP `401 Unauthorized`

### 4.2 С невалидным токеном
```bash
curl -s -H "Authorization: Bearer invalid" http://localhost/files/
```
- [ ] HTTP `401 Unauthorized`

### 4.3 С валидным токеном
```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost/files/
```
- [ ] HTTP `200 OK`
- [ ] Тело: `{"files":[]}` (или список файлов, если есть)

## 5. CRUD файлов (через API)

### 5.1 Загрузка файла
```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -F "file=@README.md" \
  http://localhost/files/upload
```
- [ ] HTTP `200 OK`
- [ ] Тело содержит `"filename":"README.md"`

### 5.2 Список файлов
```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost/files/
```
- [ ] Тело содержит `"README.md"` в массиве `files`

### 5.3 Получение presigned URL
```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost/files/README.md
```
- [ ] HTTP `200 OK`
- [ ] Тело содержит `"url"` (ссылка на MinIO)
- [ ] Переход по ссылке отдаёт файл (HTTP `200` + содержимое)

### 5.4 Удаление файла
```bash
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" http://localhost/files/README.md
```
- [ ] HTTP `200 OK`
- [ ] Повторный `GET /files/` не содержит `README.md`

## 6. React frontend (браузер)

- [ ] Открыть `http://localhost/` в браузере.
- [ ] Отображается страница логина (форма username/password).
- [ ] Ввести `demo` / `demopass` → успешный вход, переход к файловому менеджеру.
- [ ] Отображается список файлов (соответствует `GET /files/`).
- [ ] Загрузка нового файла через UI → файл появляется в списке.
- [ ] Нажатие "Download" → открывается presigned URL, файл скачивается.
- [ ] Нажатие "Delete" → файл исчезает из списка.
- [ ] Обновление страницы (F5) → токен восстанавливается из `localStorage`, пользователь остаётся авторизован.
- [ ] Нажатие "Logout" → токен удаляется, возврат на страницу логина.

## 7. Безопасность и логи

- [ ] В логах контейнера `tacitus-app` (`docker compose logs app`) **отсутствуют**:
  - JWT-токены
  - Пароли в plaintext
  - S3 secret keys
- [ ] В логах присутствуют:
  - `request_id`
  - Маршрут (`route`)
  - HTTP статус
  - Имя пользователя (без пароля)

## 8. MinIO Console (опционально)

- [ ] Открыть `http://localhost:9001` → доступна консоль MinIO.
- [ ] Войти с `minioadmin` / `minioadmin`.
- [ ] Bucket `tacitus-files` существует и содержит загруженные файлы.

## 9. Остановка стека

```bash
docker compose down -v
```
- [ ] Все контейнеры остановлены.
- [ ] Volume `minio-data` удалён (флаг `-v`).

---

**Результат:** Если все пункты отмечены — шаг 3.11 пройден, проект готов к продакшен-деплою.
