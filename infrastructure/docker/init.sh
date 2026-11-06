#!/bin/sh
# One-shot Compose startup gate. Every long-running application service waits
# for this script to validate configuration and apply migrations successfully.
set -eu

python - <<'PY'
import os

import django
from django.conf import settings

from core.security.config import validate_bot_security_configuration

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.local")
django.setup()

validate_bot_security_configuration(
    encryption_keys=settings.BOT_TOKEN_ENCRYPTION_KEYS,
    current_key_id=settings.BOT_TOKEN_CURRENT_KEY_ID,
    fingerprint_key=settings.TOKEN_FINGERPRINT_KEY,
)
print("==> Bot token security configuration is valid")
PY

if [ "${DJANGO_SETTINGS_MODULE:-}" = "core.settings.production" ]; then
    echo "==> Checking production settings"
    python manage.py check --deploy --fail-level WARNING
else
    echo "==> Checking local settings"
    python manage.py check
fi

echo "==> Applying migrations"
python manage.py migrate --noinput

echo "==> Synchronizing code-owned system templates"
python manage.py sync_system_templates

echo "==> Synchronizing partner-owner admin access"
python manage.py sync_partner_owner_access

if [ "${DJANGO_SETTINGS_MODULE:-}" = "core.settings.production" ]; then
    exit 0
fi

case "${DEMO_SEED:-true}" in
    false|0|no)
        echo "==> Local pilot demo is disabled (DEMO_SEED=${DEMO_SEED})"
        ;;
    *)
        echo "==> Ensuring local pilot demo"
        python manage.py seed_pilot_demo
        ;;
esac
