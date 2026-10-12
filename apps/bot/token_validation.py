from __future__ import annotations

import re

from django.core.exceptions import ValidationError

TELEGRAM_BOT_TOKEN_RE = re.compile(r"^\d{5,20}:[A-Za-z0-9_-]{20,}$")


def validate_bot_token_format(raw_token: str) -> str:
    """Validate obvious Telegram bot token shape without network I/O."""

    token = raw_token.strip()
    if not TELEGRAM_BOT_TOKEN_RE.fullmatch(token):
        raise ValidationError("Telegram bot token has an invalid format.")
    return token
