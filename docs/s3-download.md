# Скачивание всех файлов из S3 (MinIO)

Инструкция описывает два способа выгрузить содержимое бакета `tacitus-files`:

1. **Через API приложения** — штатный путь, работает снаружи, требует JWT.
2. **Напрямую через MinIO (`mc` / `aws s3`)** — административный путь, работает только там, где доступен сам MinIO
   (внутри docker-сети или на сервере).

## Что нужно знать заранее

| Ограничение                    | Значение        | Где задано                                                              |
|--------------------------------|-----------------|-------------------------------------------------------------------------|
| TTL access-токена              | 15 минут        | `jwt_expiration_minutes`, [app/core/config.py](../app/core/config.py)   |
| TTL presigned-ссылки           | 300 секунд      | `presigned_url_expiration`, [app/core/config.py](../app/core/config.py) |
| Rate limit `GET /files/`       | 60 запросов/мин | [app/api/routers/files.py](../app/api/routers/files.py)                 |
| Rate limit `GET /files/{name}` | 30 запросов/мин | [app/api/routers/files.py](../app/api/routers/files.py)                 |
| Порты MinIO наружу             | не публикуются  | [docker-compose.yml](../docker-compose.yml)                             |

Два практических следствия:

- **Вложенные ключи через API недоступны.** `sanitize_filename` отбрасывает путь и оставляет только имя файла, поэтому
  объекты вида
  `mediamtx-archives/cam1/2026-09-07.mp4` (их пишет
  [scripts/mediamtx-archive-manager.py](../scripts/mediamtx-archive-manager.py))
  вернут `404`. `GET /files/` их покажет, скачать через API — нет. Для архивов записей используйте способ 2.
- **Скачивание пачкой упирается в rate limit 30/мин.** Скрипты ниже делают паузу между запросами; без неё сервер ответит
  `429`.

## Способ 1. Через API

Базовый URL снаружи — `https://<host>/api` (nginx проксирует `/api/` в приложение), локально — `http://localhost:8000`.

Последовательность: логин → список файлов → presigned-ссылка на каждый файл → скачивание по ссылке напрямую из S3.

### Шаг 1. Получить токен

```bash
curl -sS -X POST "$BASE_URL/auth/login" -H 'Content-Type: application/json' -d '{"username":"'"$TACITUS_USER"'","password":"'"$TACITUS_PASSWORD"'"}'
```

Ответ содержит `access_token` и `refresh_token`.

### Шаг 2. Получить список объектов

```bash
curl -sS "$BASE_URL/files/" -H "Authorization: Bearer $TOKEN"
```

Ответ: `{"files": ["report.pdf", "photo.jpg", "mediamtx-archives/cam1/rec.mp4"]}`. Пагинация обрабатывается на сервере,
список полный.

### Шаг 3. Получить presigned-ссылку и скачать

```bash
curl -sS "$BASE_URL/files/report.pdf" -H "Authorization: Bearer $TOKEN"
```

Ответ: `{"filename": "report.pdf", "url": "http://minio:9000/..."}`. Ссылка действует 5 минут, отдельные учётные данные
S3 клиенту не нужны.

> Если `S3_ENDPOINT` указывает на внутреннее имя `minio`, presigned-URL снаружи
> не откроется — DNS-имя недоступно с рабочей машины. Для внешнего доступа
> `S3_ENDPOINT` должен быть публичным адресом MinIO. Это первое, что нужно
> проверить, если ссылки получаются, а скачивание падает по таймауту.

### Скрипт: выгрузить всё, что доступно через API

Не требует boto3, только `requests`. Пропускает вложенные ключи (они недоступны по API) и держит паузу под rate limit.

```python
"""Скачивание всех файлов бакета через API Tacitus."""

import os
import pathlib
import time

import requests

BASE_URL = os.environ["BASE_URL"].rstrip("/")  # напр. https://example.com/api
USERNAME = os.environ["TACITUS_USER"]
PASSWORD = os.environ["TACITUS_PASSWORD"]
OUT_DIR = pathlib.Path(os.environ.get("OUT_DIR", "./s3-dump"))
# GET /files/{name} ограничен 30 запросами в минуту.
DELAY_SECONDS = 2.1

session = requests.Session()
tokens = session.post(
    f"{BASE_URL}/auth/login",
    json={"username": USERNAME, "password": PASSWORD},
    timeout=10,
).json()
headers = {"Authorization": f"Bearer {tokens['access_token']}"}

files = session.get(f"{BASE_URL}/files/", headers=headers, timeout=30).json()["files"]
OUT_DIR.mkdir(parents=True, exist_ok=True)

for key in files:
    if "/" in key:
        print(f"skip (вложенный ключ, недоступен через API): {key}")
        continue

    meta = session.get(f"{BASE_URL}/files/{key}", headers=headers, timeout=30)
    if meta.status_code != 200:
        print(f"skip {key}: HTTP {meta.status_code}")
        time.sleep(DELAY_SECONDS)
        continue

    # Presigned-URL уже подписан, заголовок Authorization слать не нужно.
    with requests.get(meta.json()["url"], stream=True, timeout=120) as resp:
        resp.raise_for_status()
        with (OUT_DIR / key).open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                fh.write(chunk)

    print(f"ok {key}")
    time.sleep(DELAY_SECONDS)
```

Запуск:

```bash
BASE_URL=https://example.com/api TACITUS_USER=admin TACITUS_PASSWORD=... OUT_DIR=./s3-dump python download_all.py
```

Если файлов много и выгрузка длится дольше 15 минут, access-токен истечёт — обновите его через `POST /auth/refresh` или
перезапустите скрипт.

## Способ 2. Напрямую из MinIO

Даёт полный дамп, включая вложенные ключи и архивы MediaMTX. Требует доступа к хосту с MinIO и учётных данных
`S3_ACCESS_KEY` / `S3_SECRET_KEY` из `.env`.

### Вариант A: `mc` в docker-сети (рекомендуется)

```bash
docker run --rm -it --network tacitus_tacitus-network -v "$PWD/s3-dump:/dump" --entrypoint sh minio/mc:RELEASE.2025-04-16T18-13-26Z -c 'mc alias set local http://minio:9000 "$KEY" "$SECRET" && mc mirror --overwrite local/tacitus-files /dump'
```

Передайте `KEY` и `SECRET` через `-e KEY=... -e SECRET=...` или `--env-file .env`; не подставляйте секреты в командную
строку — они попадут в history. Точное имя сети смотрите в `docker network ls` (обычно `<project>_tacitus-network`).

### Вариант B: с сервера через `aws s3`

```bash
AWS_ACCESS_KEY_ID="$S3_ACCESS_KEY" AWS_SECRET_ACCESS_KEY="$S3_SECRET_KEY" aws --endpoint-url "$S3_ENDPOINT" s3 sync s3://tacitus-files ./s3-dump
```

## Проверка результата

```bash
curl -sS "$BASE_URL/files/stats" -H "Authorization: Bearer $TOKEN"
```

`total_files` и `total_size_bytes` из `GET /files/stats` должны совпасть с тем, что лежит в каталоге выгрузки:

```bash
find ./s3-dump -type f | wc -l && du -sb ./s3-dump
```

Расхождение при выгрузке через API — ожидаемо ровно на число вложенных ключей.

## Риски

- Выгрузка снимает данные из защищённого хранилища на рабочую машину — каталог дампа не должен попасть в git
  (`.gitignore`) и в публичные каталоги.
- Presigned-ссылки живут 5 минут, но действительны без аутентификации: не логируйте их и не пересылайте.
- `mc mirror --overwrite` перезаписывает локальные файлы; в обратную сторону (`/dump` → бакет) команду не запускать —
  это перезапишет хранилище.
- Массовая выгрузка фиксируется в security-логе как события `file.download`.
