from __future__ import annotations


def normalize_phone(value: str) -> str:
    """Normalize phone enough for matching while avoiding country-specific guesses."""

    stripped = (value or "").strip()
    if not stripped:
        return ""

    digits = "".join(char for char in stripped if char.isdigit())
    if not digits:
        return ""

    prefix = "+" if stripped.startswith("+") else ""
    return f"{prefix}{digits}"
