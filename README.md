# BoteraMaster

BoteraMaster is a planned Django modular monolith for Telegram-first scheduling,
CRM, reminders, messaging, broadcasts, and lightweight finance for solo masters.

The repository is in approved early MVP implementation. Foundation,
Tenant/Admin, Bot Runtime, Identity/Catalog, Schedule, Appointment Core, and
Client Booking Flow foundations are in place. See `AGENTS.md` and
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
```
