from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parents[2]


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: list[str] | None = None) -> list[str]:
    value = os.getenv(name)
    if value is None:
        return default or []
    return [item.strip() for item in value.split(",") if item.strip()]


def database_config() -> dict:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        parsed = urlparse(database_url)
        if parsed.scheme not in {"postgres", "postgresql"}:
            raise ValueError("DATABASE_URL must use postgres/postgresql for production.")
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": parsed.path.removeprefix("/"),
            "USER": parsed.username or "",
            "PASSWORD": parsed.password or "",
            "HOST": parsed.hostname or "",
            "PORT": parsed.port or "",
        }

    postgres_db = os.getenv("POSTGRES_DB")
    if postgres_db:
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": postgres_db,
            "USER": os.getenv("POSTGRES_USER", ""),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD", ""),
            "HOST": os.getenv("POSTGRES_HOST", "localhost"),
            "PORT": os.getenv("POSTGRES_PORT", "5432"),
        }

    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }


def parse_key_ring(raw_value: str | None) -> dict[str, str]:
    if not raw_value:
        return {}
    result: dict[str, str] = {}
    for item in raw_value.split(","):
        if not item.strip():
            continue
        key_id, separator, key = item.partition(":")
        if not separator:
            raise ValueError("Encryption keys must use key-id:fernet-key format.")
        result[key_id.strip()] = key.strip()
    return result
