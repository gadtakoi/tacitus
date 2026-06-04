# Frontend

## Технологии

- **React** 18
- **TypeScript**
- **Vite** — сборка
- **Fetch API** — HTTP клиент

## Структура

```
frontend/src/
├── api/
│   └── client.ts            # API клиент с JWT injection
├── components/              # React компоненты
├── pages/                   # Страницы (login, file manager)
└── App.tsx                  # Root компонент
```

## API клиент

- Базовый URL: `/api` (через Nginx proxy)
- Автоматически добавляет `Authorization: Bearer <token>` из `localStorage`
- `authApi.login()` / `authApi.register()` — отправляют JSON body
- `filesApi.list()` / `upload()` / `getUrl()` / `delete()`

## Сборка

```bash
cd frontend
npm install
npm run build
```

Сборка встраивается в Docker образ `tacitus-nginx` через multi-stage build.
