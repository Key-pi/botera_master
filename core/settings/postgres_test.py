"""Deterministic test settings that keep the Compose PostgreSQL connection."""

from core.settings.local import *  # noqa: F403

LANGUAGE_CODE = "en"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
