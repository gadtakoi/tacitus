# План проекта Tacitus — интеграция MediaMTX

## 1. Цель

Добавить в проект **Tacitus** медиа-сервер **MediaMTX** для приёма, ретрансляции и
просмотра RTSP/RTP-видеопотоков. MediaMTX запускается как отдельный сервис в
Docker Compose, получает настройки через `.env`, проксируется Nginx, а его
развёртывание на удалённом сервере автоматизируется через Ansible.

**Конечный результат:**
- RTSP-камера (или источник) публикует поток на `rtsp://<host>:8554/<path>`.
- Пользователь может просматривать поток через WebRTC (`/webrtc/<path>`) или HLS
  (`/hls/<path>/index.m3u8`) через Nginx.
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

| Компонент    | Назначение                                              |
|--------------|---------------------------------------------------------|
| **Nginx**    | Обратный прокси, терминация HTTP, проксирование WebRTC  |
| **FastAPI**  | ASGI-приложение: API, auth, S3-интеграция               |
| **React**    | SPA для просмотра и управления файлами в S3             |
| **MinIO**    | S3-совместимое объектное хранилище                      |
| **MediaMTX** | RTSP/RTMP/WebRTC/SRT медиа-сервер для видеопотоков      |
| **Docker**   | Контейнеризация сервисов                                |
| **Ansible**  | Автоматизация подготовки хоста и запуска стека          |

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

- [ ] Создать `mediamtx/mediamtx.yml` (или конфигурация через env-переменные):
  - [ ] `logLevel: info`
  - [ ] `rtspAddress: :8554`
  - [ ] `rtmpAddress: :1935`
  - [ ] `hlsAddress: :8888`
  - [ ] `webrtcAddress: :8889`
  - [ ] `srtAddress: :8890`
  - [ ] `paths:` — секция с примерами путей (например, `cam1:` с `source: publisher`)
- [ ] Проброс `mediamtx.yml` в контейнер через volume (если используется файл)
  или полная конфигурация через env-переменные (`MTX_*`)
- [ ] Убедиться, что MediaMTX запускается без ошибок в стеке `docker compose up`

### 3.3. Интеграция с Nginx

- [ ] Обновить `nginx/nginx.conf`:
  - [ ] Проксирование HLS: `/hls/` → `mediamtx:8888`
  - [ ] Проксирование WebRTC: `/webrtc/` → `mediamtx:8889`
  - [ ] Проксирование RTSP over HTTP (если нужно): `/rtsp/` → `mediamtx:8554`
  - [ ] Заголовки `X-Forwarded-*` для WebRTC
  - [ ] CORS-заголовки для HLS/WebRTC (если нужно)
- [ ] Обновить `nginx/Dockerfile` (если нужно) — обычно не требуется
- [ ] Проверить, что Nginx корректно проксирует потоки:
  - [ ] `curl http://localhost/hls/cam1/index.m3u8` → 200 OK
  - [ ] `curl http://localhost/webrtc/cam1` → 200 OK

### 3.4. API FastAPI для управления потоками (опционально, MVP)

- [ ] Добавить эндпоинты (если требуется управление путями через API):
  - [ ] `GET /streams` — список активных потоков (запрос к MediaMTX API)
  - [ ] `GET /streams/{path}` — информация о конкретном потоке
- [ ] Интеграция с auth: доступ к `/streams/*` только с валидным JWT
- [ ] Pydantic-схемы для ответов MediaMTX

### 3.5. Ansible — развёртывание MediaMTX

- [ ] Создать роль `ansible/roles/mediamtx/`:
  - [ ] `tasks/main.yml`:
    - [ ] Убедиться, что Docker установлен (зависимость от `docker_install`)
    - [ ] Создать директорию для конфигурации MediaMTX (`/opt/tacitus/mediamtx/`)
    - [ ] Копировать `mediamtx.yml` на хост (если используется файл конфигурации)
    - [ ] Добавить сервис `mediamtx` в `docker-compose.yml` (или отдельный compose-файл)
    - [ ] Запустить `docker compose up -d mediamtx`
  - [ ] `templates/mediamtx.yml.j2` — шаблон конфигурации с переменными из `group_vars`:
    - [ ] `rtsp_port` — порт RTSP
    - [ ] `rtmp_port` — порт RTMP
    - [ ] `hls_port` — порт HLS
    - [ ] `webrtc_port` — порт WebRTC
    - [ ] `srt_port` — порт SRT
    - [ ] `paths` — список путей/камер из inventory
  - [ ] `defaults/main.yml` — дефолты для роли
- [ ] Обновить `ansible/playbook.yml`:
  - [ ] Добавить роль `mediamtx` после `app_deploy`
- [ ] Обновить `ansible/group_vars/all.yml` (или создать `mediamtx.yml`):
  - [ ] `mediamtx_rtsp_port: 8554`
  - [ ] `mediamtx_rtmp_port: 1935`
  - [ ] `mediamtx_hls_port: 8888`
  - [ ] `mediamtx_webrtc_port: 8889`
  - [ ] `mediamtx_srt_port: 8890`
  - [ ] `mediamtx_ice_port: 8189`
  - [ ] `mediamtx_paths:` — список камер/путей
- [ ] Обновить `ansible/inventory/hosts.yml` (опционально):
  - [ ] Добавить переменные хоста для конкретных камер
- [ ] Pre-checks / post-checks:
  - [ ] Проверка, что порты свободны перед деплоем
  - [ ] Проверка `curl http://localhost:8888` (HLS endpoint) после деплоя
  - [ ] Проверка `docker ps` — контейнер `tacitus-mediamtx` в статусе `healthy`

### 3.6. Запись потоков и управление архивами

- [ ] Настроить запись входящих RTSP-потоков в файлы на сервере:
  - [ ] Включить `record: yes` (или `runOnPublish` с ffmpeg) в `mediamtx.yml` для нужных paths
  - [ ] Настроить формат записи: сегменты по времени (`segmentDuration`) или один файл
  - [ ] Указать директорию для записи (`recordPath`) и пробросить её как volume в Docker Compose
  - [ ] Обеспечить права на запись для non-root пользователя контейнера MediaMTX
- [ ] Отключить ретрансляцию по умолчанию:
  - [ ] Отключить HLS (`hls: no` или не экспонировать порт `8888`)
  - [ ] Отключить WebRTC (`webrtc: no` или не экспонировать порт `8889`)
  - [ ] Отключить RTMP (`rtmp: no` или не экспонировать порт `1935`)
  - [ ] Отключить SRT (`srt: no` или не экспонировать порт `8890`)
  - [ ] Оставить только RTSP-приём (`rtsp: yes`) и запись
- [ ] Настроить ограничения хранения архивов:
  - [ ] Ограничение по суммарному размеру архива (ротация/удаление при превышении лимита)
  - [ ] Ограничение по времени хранения (max age, автоудаление сегментов старше N дней/часов)
  - [ ] Реализация ротации: встроенный механизм MediaMTX (если есть) или внешний скрипт/cron
  - [ ] Интеграция с S3/MinIO: по достижении лимита локального хранения — выгрузка старых архивов в S3

### 3.7. Тестирование и верификация

- [ ] Локальное тестирование Docker Compose:
  - [ ] `docker compose up` поднимает полный стек (app + nginx + minio + frontend + mediamtx)
  - [ ] MediaMTX слушает RTSP на порту `8554`
  - [ ] FFmpeg может опубликовать тестовый поток:
    `ffmpeg -re -f lavfi -i testsrc=duration=60:size=640x480:rate=30 -f rtsp rtsp://localhost:8554/live`
  - [ ] HLS плейлист доступен: `curl http://localhost/hls/live/index.m3u8` → 200
  - [ ] WebRTC страница доступна: `curl http://localhost/webrtc/live` → 200
- [ ] Интеграционные тесты Ansible:
  - [ ] Playbook выполняется без ошибок на тестовом хосте
  - [ ] Повторный прогон подтверждает идемпотентность (`changed=0`)
- [ ] Security checklist:
  - [ ] Порты MediaMTX не открыты наружу напрямую (только через Nginx, кроме RTSP)
  - [ ] Нет hardcoded credentials в `mediamtx.yml`
  - [ ] `.env` содержит чувствительные данные и в `.gitignore`
  - [ ] RTSP UDP порты (8000/8001) не экспонируются без необходимости

---

## 4. Технологический стек

| Слой           | Технология                 | Версия / Требование                 |
|----------------|----------------------------|-------------------------------------|
| Media Server   | MediaMTX                   | `bluenviron/mediamtx`, pinned       |
| Streaming      | RTSP / RTMP / HLS / WebRTC | Протоколы MediaMTX                  |
| Proxy          | Nginx                      | Stable, pinned version              |
| Контейнеры     | Docker + Compose           | Docker Engine >= 24.x               |
| Автоматизация  | Ansible                    | >= 2.15, core                       |
| Тестирование   | pytest + ffmpeg            | ffmpeg для генерации тестовых потоков |

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

| Этап                             | Статус        | Дата начала | Дата окончания |
|----------------------------------|---------------|-------------|----------------|
| 3.1 MediaMTX в Docker Compose    | [ ]           |             |                |
| 3.2 Конфигурация MediaMTX        | [ ]           |             |                |
| 3.3 Интеграция с Nginx           | [ ]           |             |                |
| 3.4 API FastAPI (опционально)    | [ ]           |             |                |
| 3.5 Ansible роль                 | [ ]           |             |                |
| 3.6 Запись потоков и управление архивами | [ ]   |             |                |
| 3.7 Тестирование и верификация   | [ ]           |             |                |

---

## 7. Риски и ограничения

| Риск                                    | Митигация                                                            |
|-----------------------------------------|----------------------------------------------------------------------|
| RTSP UDP не работает в Docker NAT       | Использовать `MTX_RTSPTRANSPORTS=tcp` или `network_mode: host`       |
| Конфликт портов с другими сервисами     | Параметризовать все порты через `.env` / `group_vars`                |
| WebRTC требует STUN/ICE                 | Настроить `MTX_WEBRTCADDITIONALHOSTS` с внешним IP                 |
| Большой размер образа с FFmpeg          | Использовать `bluenviron/mediamtx:latest` без FFmpeg, если не нужен |
| Отсутствие rollback в Ansible           | Добавить `docker compose down` + backup перед деплоем                |

---

## 8. Критерии завершения (Definition of Done)

- [ ] `docker compose up` поднимает стек с MediaMTX без ошибок.
- [ ] RTSP-порт настраивается через `.env` (проверить смену порта).
- [ ] FFmpeg может опубликовать тестовый поток на RTSP endpoint.
- [ ] HLS плейлист доступен через Nginx (`/hls/<path>/index.m3u8`).
- [ ] WebRTC страница доступна через Nginx (`/webrtc/<path>`).
- [ ] Ansible playbook выполняется без ошибок на чистом хосте.
- [ ] Повторный прогон Ansible не вносит изменений (идемпотентность).
- [ ] Нет hardcoded credentials в коде и конфигурации.
- [ ] Все файлы соответствуют `.ai/*` правилам проекта.
