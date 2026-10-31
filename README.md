# BoteraMaster

BoteraMaster is a planned Django modular monolith for Telegram-first scheduling,
CRM, reminders, messaging, broadcasts, and lightweight finance for solo masters.

The explicitly approved MVP milestones, including Pilot Wiring and the Docker
production ingress baseline, are implemented. See `AGENTS.md` and
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
./scripts/compose.sh dev up --build -d
./scripts/compose.sh dev ps
./scripts/compose.sh dev logs init
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
./scripts/compose.sh prod config --quiet
./scripts/compose.sh prod up --build -d
```

Set every production-only value documented in `.env.production.example`. DNS must
point `BOTERA_DOMAIN` to the server and public TCP ports `80/443` must reach
Caddy. Django, PostgreSQL, and Redis are not published by the production
overlay. See `docs/spec/RUNBOOK_DRAFT.md` for the complete deploy and recovery
procedure.

The mode launcher is intentionally strict: `dev` always uses `.env` and local
settings, while `prod` always uses `.env.production`, the production settings,
and a separate `botera-master-production` Compose project.
