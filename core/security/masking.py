from __future__ import annotations

SENSITIVE_KEY_PARTS = ("token", "phone", "message", "note", "secret", "password")


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


def redact_mapping(value: dict) -> dict:
    redacted = {}
    for key, item in value.items():
        normalized_key = str(key).lower()
        if any(part in normalized_key for part in SENSITIVE_KEY_PARTS):
            redacted[key] = "***"
        elif isinstance(item, dict):
            redacted[key] = redact_mapping(item)
        else:
            redacted[key] = item
    return redacted
