from django.core.exceptions import ImproperlyConfigured

from core.security.config import validate_bot_security_configuration
from core.settings import base as base_settings
from core.settings.base import *  # noqa: F403

DEBUG = False

if base_settings.SECRET_KEY in {"change-me", "botera-master-development-secret"} or len(
    base_settings.SECRET_KEY
) < 50:
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be a production-only random value of at least 50 characters."
    )

if not base_settings.ALLOWED_HOSTS or "*" in base_settings.ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        "DJANGO_ALLOWED_HOSTS must contain explicit production hostnames."
    )

if not base_settings.CSRF_TRUSTED_ORIGINS or any(
    not origin.startswith("https://") for origin in base_settings.CSRF_TRUSTED_ORIGINS
):
    raise ImproperlyConfigured(
        "DJANGO_CSRF_TRUSTED_ORIGINS must contain explicit HTTPS origins."
    )

database = base_settings.DATABASES["default"]
database_password = str(database.get("PASSWORD", ""))
if database["ENGINE"] == "django.db.backends.postgresql" and (
    database_password in {"botera_master", "change-me"} or len(database_password) < 16
):
    raise ImproperlyConfigured(
        "POSTGRES_PASSWORD must be a production-only value of at least 16 characters."
    )

validate_bot_security_configuration(
    encryption_keys=base_settings.BOT_TOKEN_ENCRYPTION_KEYS,
    current_key_id=base_settings.BOT_TOKEN_CURRENT_KEY_ID,
    fingerprint_key=base_settings.TOKEN_FINGERPRINT_KEY,
)

# The application port is private to the Compose network. Only Caddy can reach
# it, so trusting Caddy's forwarded scheme is safe in this deployment shape.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

# Botera may later use sibling subdomains for separate products. Do not assert
# includeSubDomains/preload until those hosts have been audited for HTTPS.
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]
