# BoteraMaster

BoteraMaster is a planned Django modular monolith for Telegram-first scheduling,
CRM, reminders, messaging, broadcasts, and lightweight finance for solo masters.

The repository is in approved early MVP implementation. The domain foundations
and the current Pilot Wiring slice are in place. See `AGENTS.md` and
`docs/spec/IMPLEMENTATION_PLAN.md` before changing code.

## Local checks

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
pytest
ruff check .
```

## Bot runtime smoke check

```bash
python manage.py run_partner_bots --dry-run
python manage.py check_bot_connection --partner-id <partner-uuid>
python manage.py check_pilot_readiness --partner-id <partner-uuid>
python manage.py check_pilot_readiness --partner-id <partner-uuid> --check-telegram
```

The commands read the encrypted token already stored for the partner. Never put
a raw bot token in a command argument, committed file, issue, or log.

## Docker Compose pilot stack

Docker is the default local workflow. Copy the example once and generate the
Fernet/fingerprint secrets using the commands beside those variables:

```bash
cp .env.example .env
docker compose up --build -d
docker compose ps
docker compose logs init
```

PostgreSQL and Redis become healthy first. The one-shot `init` service validates
token-security settings, applies migrations, synchronizes owner permissions,
and ensures an idempotent local `Preview Studio` demo. Only then do `web`,
`bot-runner`, `celery-beat`, isolated notification/broadcast workers, and the
default/maintenance worker start.

Admin is at `http://127.0.0.1:8001/admin/`. Demo usernames and passwords are in
the ignored `.env`; `init` logs the usernames without printing passwords.
PostgreSQL and Redis are internal by default. Use
`infrastructure/docker/compose.ports.yml` only when a host-side database or
Redis client needs loopback access.

## Production Compose ingress

Public production deploys use the reviewed overlay with Gunicorn and Caddy:

```bash
cp .env.production.example .env.production
docker compose --env-file .env.production \
  -f docker-compose.yml -f docker-compose.production.yml config --quiet
docker compose --env-file .env.production \
  -f docker-compose.yml -f docker-compose.production.yml up --build -d
```

Set every production-only value documented in `.env.production.example`. DNS must
point `BOTERA_DOMAIN` to the server and public TCP ports `80/443` must reach
Caddy. Django, PostgreSQL, and Redis are not published by the production
overlay. See `docs/spec/RUNBOOK_DRAFT.md` for the complete deploy and recovery
procedure.
