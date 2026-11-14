from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Europe/Kyiv"
TIMEZONE_ALIASES = {
    "Europe/Kiev": "Europe/Kyiv",
}


def normalize_timezone_name(timezone_name: str | None) -> str:
    requested_name = DEFAULT_TIMEZONE if timezone_name is None else timezone_name.strip()
    return TIMEZONE_ALIASES.get(requested_name, requested_name)


def get_zone(timezone_name: str | None) -> ZoneInfo:
    return ZoneInfo(normalize_timezone_name(timezone_name))


def to_partner_time(value: datetime, timezone_name: str | None) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Datetime must be timezone-aware.")
    return value.astimezone(get_zone(timezone_name))
