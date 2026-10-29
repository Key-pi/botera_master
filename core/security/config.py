from __future__ import annotations

from collections.abc import Mapping

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured

MIN_FINGERPRINT_KEY_BYTES = 32


def validate_bot_security_configuration(
    *,
    encryption_keys: Mapping[str, str],
    current_key_id: str,
    fingerprint_key: str,
) -> None:
    """Fail safely when production bot-token secrets cannot protect tokens."""

    if not encryption_keys:
        raise ImproperlyConfigured("BOT_TOKEN_ENCRYPTION_KEYS must be configured.")

    for key_id, key in encryption_keys.items():
        if not key_id or not key:
            raise ImproperlyConfigured(
                "BOT_TOKEN_ENCRYPTION_KEYS must contain non-empty key ids and keys."
            )
        try:
            Fernet(key.encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise ImproperlyConfigured(
                f"Bot token encryption key {key_id!r} is not a valid Fernet key."
            ) from exc

    if not current_key_id:
        raise ImproperlyConfigured("BOT_TOKEN_CURRENT_KEY_ID must be configured.")
    if current_key_id not in encryption_keys:
        raise ImproperlyConfigured(
            "BOT_TOKEN_CURRENT_KEY_ID must reference a configured encryption key."
        )

    if len(fingerprint_key.encode("utf-8")) < MIN_FINGERPRINT_KEY_BYTES:
        raise ImproperlyConfigured(
            "TOKEN_FINGERPRINT_KEY must contain at least "
            f"{MIN_FINGERPRINT_KEY_BYTES} bytes of independent secret material."
        )
    if fingerprint_key in encryption_keys.values():
        raise ImproperlyConfigured(
            "TOKEN_FINGERPRINT_KEY must be different from bot token encryption keys."
        )
