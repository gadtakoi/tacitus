# Frontend

## Технологии

- **React** 19
- **TypeScript** 6
- **Vite** 8 — сборка
- **Fetch API** — HTTP клиент

## Структура

```
frontend/src/
├── api/
│   └── client.ts            # API клиент с JWT injection
├── components/              # React компоненты
│   └── AppHeader.tsx        # Общая шапка: версия сервиса, навигация, выход
├── pages/                   # Страницы (login, file manager)
└── App.tsx                  # Root компонент
```

## API клиент

- Базовый URL: `/api` (через Nginx proxy)
- Автоматически добавляет `Authorization: Bearer <token>` из `localStorage`
- `authApi.login()` / `authApi.register()` — отправляют JSON body
- `filesApi.list()` / `upload()` / `getUrl()` / `delete()`
- `settingsApi.list()` / `version()` — конфигурация и версия сервиса

`AppHeader` запрашивает `settingsApi.version()` при монтировании и показывает
результат рядом с названием. Отказ запроса не обрабатывается как ошибка:
версия просто не отображается — шапка нужна для навигации и выхода, и падать
из-за справочного числа она не должна.

## Сборка

```bash
cd frontend
npm ci          # ставит ровно то, что записано в package-lock.json
npm run build
```

Сборка встраивается в Docker образ `tacitus-nginx` через multi-stage build.
Образ использует **`npm ci`**, а не `npm install`: первый ставит ровно дерево
из `package-lock.json` и отказывается работать, если lock разошёлся с
`package.json`. Раньше в образ копировался только `package.json`, lock-файл в
сборке не участвовал, и два билда одного коммита могли получить разные версии
React, Vite или Mantine.

> Меняя зависимости, коммитьте обновлённый `package-lock.json` вместе с
> `package.json`. Расхождение ломает сборку образа; оно же проверяется тестом
> `test_frontend_lockfile_agrees_with_package_json`.

Локально `npm install` уместен — он обновит lock-файл; в образ идёт только
`npm ci`.
