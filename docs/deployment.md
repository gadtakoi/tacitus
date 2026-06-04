# Деплой

## Локальный запуск

```bash
docker compose up -d
```

## Ansible

Плейбук: `ansible/playbook.yml`

Роли:
- `docker_install` — установка Docker Engine
- `app_deploy` — клонирование репозитория, `docker compose up`

```bash
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbook.yml
```

## Переменные окружения (production)

Все dev defaults в `app/core/config.py` должны быть переопределены:

```bash
TACITUS_JWT_SECRET=<strong-random-secret>
TACITUS_S3_ACCESS_KEY=<minio-access-key>
TACITUS_S3_SECRET_KEY=<minio-secret-key>
```

## Vault

Согласно `.ai/modules/vault.md`:
- Секреты хранить только в Vault или утверждённом secret manager
- Никогда не коммитить `.env` (уже в `.gitignore`)
- Не логировать секреты
