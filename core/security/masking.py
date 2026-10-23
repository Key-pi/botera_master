from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

SENSITIVE_KEY_PARTS = ("token", "phone", "message", "note", "secret", "password")
TELEGRAM_BOT_TOKEN_RE = re.compile(r"\b\d{5,20}:[A-Za-z0-9_-]{20,}\b")
PHONE_LIKE_RE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?![\w:])")


def mask_token(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return ""
    prefix, separator, suffix = stripped.partition(":")
    if separator:
        return f"{prefix}:***{suffix[-4:]}" if suffix else f"{prefix}:***"
    return f"***{stripped[-4:]}" if len(stripped) > 4 else "***"


def mask_phone(value: str) -> str:
    digits = "".join(char for char in value if char.isdigit())
    if len(digits) <= 4:
        return "***"
    return f"***{digits[-4:]}"


def mask_text(value: str, *, visible: int = 0) -> str:
    if not value:
        return ""
    if visible <= 0:
        return "***"
    return f"{value[:visible]}***"


def mask_sensitive_text(value: str) -> str:
    """Mask known sensitive patterns inside otherwise useful diagnostic text."""

    masked = TELEGRAM_BOT_TOKEN_RE.sub(lambda match: mask_token(match.group(0)), value)
    return PHONE_LIKE_RE.sub(lambda match: mask_phone(match.group(0)), masked)


def redact_mapping(value: dict) -> dict:
    redacted = {}
    for key, item in value.items():
        normalized_key = str(key).lower()
        if any(part in normalized_key for part in SENSITIVE_KEY_PARTS):
            redacted[key] = "***"
        elif isinstance(item, dict):
            redacted[key] = redact_mapping(item)
        elif isinstance(item, str):
            redacted[key] = mask_sensitive_text(item)
        else:
            redacted[key] = item
    return redacted


def redact_diagnostic_value(value: Any) -> Any:
    """Recursively redact sensitive values before durable diagnostics/audit storage."""

    if isinstance(value, Mapping):
        redacted = {}
        for key, item in value.items():
            normalized_key = str(key).lower()
            if any(part in normalized_key for part in SENSITIVE_KEY_PARTS):
                redacted[key] = "***"
            else:
                redacted[key] = redact_diagnostic_value(item)
        return redacted
    if isinstance(value, str):
        return mask_sensitive_text(value)
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [redact_diagnostic_value(item) for item in value]
    return value
