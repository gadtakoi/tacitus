# План проекта Tacitus — интеграция MediaMTX

## 1. Цель

Добавить в проект **Tacitus** медиа-сервер **MediaMTX** для приёма, ретрансляции и просмотра RTSP/RTP-видеопотоков.
MediaMTX запускается как отдельный сервис в Docker Compose, получает настройки через `.env`, проксируется Nginx, а его
развёртывание на удалённом сервере автоматизируется через Ansible.

**Конечный результат:**

- RTSP-камера (или источник) публикует поток на `rtsp://<host>:8554/<path>`.
- Пользователь может просматривать поток через WebRTC (`/webrtc/<path>`) или HLS (`/hls/<path>/index.m3u8`) через Nginx.
- Все порты и пути конфигурируются через `.env`.
- Ansible умеет разворачивать и настраивать MediaMTX на целевом хосте.

---

## 2. Архитектура

```
┌─────────────┐
│   Client    │
└──────┬──────┘
       │ HTTP / RTSP / WebRTC
       ▼
┌─────────────┐     ┌───────────────────┐     ┌─────────────┐
│    Nginx    │────▶│  FastAPI (Uvicorn)│────▶│    S3 /     │
│  (reverse   │     │   API + Auth      │     │   MinIO     │
│   proxy)    │     └───────────────────┘     └─────────────┘
│             │              │
│             │              ▼
│             │     ┌───────────────────┐
│             │     │   React SPA       │
│             │     │   (file manager)  │
│             │     └───────────────────┘
│             │
│             │     ┌───────────────────┐
│             └────▶│   MediaMTX        │
│                   │   (RTSP/WebRTC)   │
│                   └───────────────────┘
└─────────────┘            ▲
       │                   │
       └───────────────────┘
          Docker Compose
```

### Компоненты

| Компонент    | Назначение                                             |
|--------------|--------------------------------------------------------|
| **Nginx**    | Обратный прокси, терминация HTTP, проксирование WebRTC |
| **FastAPI**  | ASGI-приложение: API, auth, S3-интеграция              |
| **React**    | SPA для просмотра и управления файлами в S3            |
| **MinIO**    | S3-совместимое объектное хранилище                     |
| **MediaMTX** | RTSP/RTMP/WebRTC/SRT медиа-сервер для видеопотоков     |
| **Docker**   | Контейнеризация сервисов                               |
| **Ansible**  | Автоматизация подготовки хоста и запуска стека         |

---

## 3. Этапы реализации

### 3.1. MediaMTX в Docker Compose

- [x] Добавить сервис `mediamtx` в `docker-compose.yml`:
    - [x] Образ `bluenviron/mediamtx:1.15.1-ffmpeg` с фиксированной версией (не `latest`)
    - [x] Переменные окружения вынесены в `.env` (префикс `TACITUS_` → `MTX_*`)
    - [x] Проброс портов: RTSP (`8554`), RTMP (`1935`), HLS (`8888`), WebRTC (`8889`), SRT (`8890`), ICE (`8189`)
    - [x] Volume для конфигурации `mediamtx.yml` (`./mediamtx/mediamtx.yml:/mediamtx.yml:ro`)
    - [x] Volume для записи архивов (`mediamtx-recordings:/recordings`)
    - [x] Healthcheck для MediaMTX (TCP-probe на RTSP-порт через `sh`)
    - [x] Сервис подключён к сети `tacitus-network`
- [x] Добавить `.env` переменные:
    - [ ] `TACITUS_MTX_RTSP_PORT` — порт для приёма RTSP (дефолт: `8554`)
    - [ ] `TACITUS_MTX_RTMP_PORT` — порт для RTMP (дефолт: `1935`)
    - [ ] `TACITUS_MTX_HLS_PORT` — порт для HLS (дефолт: `8888`)
    - [ ] `TACITUS_MTX_WEBRTC_PORT` — порт для WebRTC HTTP (дефолт: `8889`)
    - [ ] `TACITUS_MTX_SRT_PORT` — порт для SRT (дефолт: `8890`)
    - [ ] `TACITUS_MTX_ICE_PORT` — порт для WebRTC ICE/UDP (дефолт: `8189`)
    - [ ] `TACITUS_MTX_RTSP_TRANSPORTS` — транспорты RTSP (`tcp` или `udp,tcp`)
    - [ ] `TACITUS_MTX_WEBRTC_ADDITIONAL_HOSTS` — доп. хосты для WebRTC
- [x] Обновить `docker-compose.yml`:
    - [x] Заменить `latest` на `bluenviron/mediamtx:1.15.1-ffmpeg`
    - [x] Параметризовать все порты через `${TACITUS_MTX_*_PORT:-default}`

### 3.2. Конфигурация MediaMTX

- [x] Создать `mediamtx/mediamtx.yml` (или конфигурация через env-переменные):
    - [x] `logLevel: info`
    - [x] `rtsp: yes` — RTSP включён
    - [x] `rtmp: no`, `hls: no`, `webrtc: no`, `srt: no` — ретрансляция отключена
    - [x] `paths:` — секция с примерами путей (`cam1:` с `source: publisher`)
- [x] Проброс `mediamtx.yml` в контейнер через volume (`./mediamtx/mediamtx.yml:/mediamtx.yml:ro`)
- [ ] Убедиться, что MediaMTX запускается без ошибок в стеке `docker compose up`

### 3.3. Интеграция с Nginx

- [x] Обновить `nginx/nginx.conf`:
    - [x] Проксирование HLS: `/hls/` → `mediamtx:8888`
    - [x] Проксирование WebRTC: `/webrtc/` → `mediamtx:8889`
    - [x] Заголовки `X-Forwarded-*` для WebRTC
    - [x] WebSocket-заголовки (`Upgrade`, `Connection`) для WebRTC-сигналинга
    - [x] CORS-заголовки для HLS/WebRTC
- [x] `nginx/Dockerfile` не требует изменений
- [ ] Проверить, что Nginx корректно проксирует потоки:
    - [ ] `curl http://localhost/hls/cam1/index.m3u8` → 200 OK (требует `hls: yes` в `mediamtx.yml`)
    - [ ] `curl http://localhost/webrtc/cam1` → 200 OK (требует `webrtc: yes` в `mediamtx.yml`)

### 3.4. API FastAPI для управления потоками (опционально, MVP)

- [x] Добавить эндпоинты (если требуется управление путями через API):
    - [x] `GET /streams` — список активных потоков (запрос к MediaMTX API `GET /v3/paths/list`)
    - [x] `GET /streams/{path}` — информация о конкретном потоке (`GET /v3/paths/get/{name}`)
- [x] Интеграция с auth: доступ к `/streams/*` только с валидным JWT (`Depends(get_current_user)`)
- [x] Pydantic-схемы для ответов MediaMTX (`StreamItem`, `StreamsListResponse`)

### 3.5. Ansible — развёртывание MediaMTX

- [x] Создать роль `ansible/roles/mediamtx/`:
    - [x] `tasks/main.yml`:
        - [x] Убедиться, что Docker установлен (зависимость от `docker_install`)
        - [x] Создать директорию для конфигурации MediaMTX (`/opt/tacitus/mediamtx/`)
        - [x] Копировать `mediamtx.yml` на хост (если используется файл конфигурации)
        - [x] Добавить сервис `mediamtx` в `docker-compose.yml` (или отдельный compose-файл)
        - [x] Запустить `docker compose up -d mediamtx`
    - [x] `templates/mediamtx.yml.j2` — шаблон конфигурации с переменными из `group_vars`:
        - [x] `rtsp_port` — порт RTSP
        - [x] `rtmp_port` — порт RTMP
        - [x] `hls_port` — порт HLS
        - [x] `webrtc_port` — порт WebRTC
        - [x] `srt_port` — порт SRT
        - [x] `paths` — список путей/камер из inventory
    - [x] `defaults/main.yml` — дефолты для роли
- [x] Обновить `ansible/playbook.yml`:
    - [x] Добавить роль `mediamtx` после `app_deploy`
- [x] Обновить `ansible/group_vars/all.yml` (или создать `mediamtx.yml`):
    - [x] `mediamtx_rtsp_port: 8554`
    - [x] `mediamtx_rtmp_port: 1935`
    - [x] `mediamtx_hls_port: 8888`
    - [x] `mediamtx_webrtc_port: 8889`
    - [x] `mediamtx_srt_port: 8890`
    - [x] `mediamtx_ice_port: 8189`
    - [x] `mediamtx_paths:` — список камер/путей
- [x] Обновить `ansible/inventory/hosts.yml` (опционально):
    - [x] Добавить переменные хоста для конкретных камер
- [x] Pre-checks / post-checks:
    - [x] Проверка, что порты свободны перед деплоем
    - [x] Проверка `curl http://localhost:8888` (HLS endpoint) после деплоя
    - [x] Проверка `docker ps` — контейнер `tacitus-mediamtx` в статусе `healthy`

### 3.6. Запись потоков и управление архивами

- [x] Настроить запись входящих RTSP-потоков в файлы на сервере:
    - [x] Включить `record: yes` (или `runOnPublish` с ffmpeg) в `mediamtx.yml` для нужных paths
    - [x] Настроить формат записи: сегменты по времени (`segmentDuration`) или один файл
    - [x] Указать директорию для записи (`recordPath`) и пробросить её как volume в Docker Compose
    - [x] Обеспечить права на запись для non-root пользователя контейнера MediaMTX
- [x] Отключить ретрансляцию по умолчанию:
    - [x] Отключить HLS (`hls: no` или не экспонировать порт `8888`)
    - [x] Отключить WebRTC (`webrtc: no` или не экспонировать порт `8889`)
    - [x] Отключить RTMP (`rtmp: no` или не экспонировать порт `1935`)
    - [x] Отключить SRT (`srt: no` или не экспонировать порт `8890`)
    - [x] Оставить только RTSP-приём (`rtsp: yes`) и запись
- [x] Настроить ограничения хранения архивов:
    - [x] Ограничение по суммарному размеру архива (ротация/удаление при превышении лимита)
    - [x] Ограничение по времени хранения (max age, автоудаление сегментов старше N дней/часов)
    - [x] Реализация ротации: встроенный механизм MediaMTX (если есть) или внешний скрипт/cron
    - [x] Интеграция с S3/MinIO: по достижении лимита локального хранения — выгрузка старых архивов в S3

### 3.7. Тестирование и верификация

- [x] Локальное тестирование Docker Compose:
    - [x] `docker compose up` поднимает полный стек (app + nginx + minio + frontend + mediamtx)
    - [x] MediaMTX слушает RTSP на порту `8554`
    - [x] FFmpeg может опубликовать тестовый поток:
      `ffmpeg -re -f lavfi -i testsrc=duration=60:size=640x480:rate=30 -f rtsp rtsp://localhost:8554/live`
    - [x] Запись потока работает: сегменты появляются в `./recordings/live/`
    - [ ] HLS плейлист доступен: `curl http://localhost/hls/live/index.m3u8` → 200 (отключено в 3.6)
    - [ ] WebRTC страница доступна: `curl http://localhost/webrtc/live` → 200 (отключено в 3.6)
- [ ] Интеграционные тесты Ansible:
    - [ ] Playbook выполняется без ошибок на тестовом хосте (не запускался в текущем окружении)
    - [ ] Повторный прогон подтверждает идемпотентность (`changed=0`)
- [x] Security checklist:
    - [x] Порты MediaMTX не открыты наружу напрямую (только через Nginx, кроме RTSP)
    - [x] Нет hardcoded credentials в `mediamtx.yml`
    - [x] `.env` содержит чувствительные данные и в `.gitignore`
    - [x] RTSP UDP порты (8000/8001) не экспонируются без необходимости

---

## 4. Технологический стек

| Слой          | Технология                 | Версия / Требование                   |
|---------------|----------------------------|---------------------------------------|
| Media Server  | MediaMTX                   | `bluenviron/mediamtx`, pinned         |
| Streaming     | RTSP / RTMP / HLS / WebRTC | Протоколы MediaMTX                    |
| Proxy         | Nginx                      | Stable, pinned version                |
| Контейнеры    | Docker + Compose           | Docker Engine >= 24.x                 |
| Автоматизация | Ansible                    | >= 2.15, core                         |
| Тестирование  | pytest + ffmpeg            | ffmpeg для генерации тестовых потоков |

---

## 5. Требования

### Функциональные (FR)

- **FR-001**: MediaMTX принимает RTSP-поток на настраиваемом порту (дефолт `8554`).
- **FR-002**: Порт для приёма RTSP потока можно изменить через `.env`.
- **FR-003**: Nginx проксирует HLS и WebRTC endpoints к MediaMTX.
- **FR-004**: Ansible разворачивает и настраивает MediaMTX на удалённом сервере.
- **FR-005**: Конфигурация путей (paths) камер задаётся через Ansible `group_vars`.

### Нефункциональные (NFR)

- **NFR-001**: Все порты MediaMTX параметризованы через `.env` / `group_vars`.
- **NFR-002**: Нет hardcoded credentials в конфигурации MediaMTX.
- **NFR-003**: Ansible playbook идемпотентен (повторный запуск не меняет состояние).
- **NFR-004**: Версия образа MediaMTX зафиксирована (не `latest`).
- **NFR-005**: RTSP UDP транспорт отключён в Docker-окружении (используется TCP) из-за NAT.

---

## 6. Сроки и контрольные точки

| Этап                                     | Статус | Дата начала | Дата окончания |
|------------------------------------------|--------|-------------|----------------|
| 3.1 MediaMTX в Docker Compose            | [ ]    |             |                |
| 3.2 Конфигурация MediaMTX                | [ ]    |             |                |
| 3.3 Интеграция с Nginx                   | [ ]    |             |                |
| 3.4 API FastAPI (опционально)            | [ ]    |             |                |
| 3.5 Ansible роль                         | [x]    | 2026-06-07  | 2026-06-07     |
| 3.6 Запись потоков и управление архивами | [x]    | 2026-06-07  | 2026-06-07     |
| 3.7 Тестирование и верификация           | [~]    | 2026-06-07  | 2026-06-07     |

---

## 7. Риски и ограничения

| Риск                                | Митигация                                                           |
|-------------------------------------|---------------------------------------------------------------------|
| RTSP UDP не работает в Docker NAT   | Использовать `MTX_RTSPTRANSPORTS=tcp` или `network_mode: host`      |
| Конфликт портов с другими сервисами | Параметризовать все порты через `.env` / `group_vars`               |
| WebRTC требует STUN/ICE             | Настроить `MTX_WEBRTCADDITIONALHOSTS` с внешним IP                  |
| Большой размер образа с FFmpeg      | Использовать `bluenviron/mediamtx:latest` без FFmpeg, если не нужен |
| Отсутствие rollback в Ansible       | Добавить `docker compose down` + backup перед деплоем               |

---

## 8. Критерии завершения (Definition of Done)

- [x] `docker compose up` поднимает стек с MediaMTX без ошибок.
- [x] RTSP-порт настраивается через `.env` (проверен на порту 8555).
- [x] FFmpeg может опубликовать тестовый поток на RTSP endpoint.
- [ ] HLS плейлист доступен через Nginx (`/hls/<path>/index.m3u8`) (отключено в 3.6).
- [ ] WebRTC страница доступна через Nginx (`/webrtc/<path>`) (отключено в 3.6).
- [ ] Ansible playbook выполняется без ошибок на чистом хосте (не запускался в текущем окружении).
- [ ] Повторный прогон Ansible не вносит изменений (идемпотентность) (не проверялась).
- [x] Нет hardcoded credentials в коде и конфигурации.
- [x] Все файлы соответствуют `.ai/*` правилам проекта.
