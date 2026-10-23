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

```bash
docker compose build
docker compose run --rm web python manage.py migrate
docker compose up -d
docker compose ps
```

The stack runs `web`, `bot-runner`, `celery-beat`, isolated notification and
broadcast workers, a default/maintenance worker, PostgreSQL, and Redis.
