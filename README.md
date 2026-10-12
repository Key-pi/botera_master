# BoteraMaster

BoteraMaster is a planned Django modular monolith for Telegram-first scheduling,
CRM, reminders, messaging, broadcasts, and lightweight finance for solo masters.

The repository is in approved early MVP implementation. Foundation is in place,
and Tenant/Admin work has started. See `AGENTS.md` and
`docs/spec/IMPLEMENTATION_PLAN.md` before changing code.

## Local checks

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
pytest
ruff check .
```
