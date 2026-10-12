from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Europe/Kiev"


def get_zone(timezone_name: str | None) -> ZoneInfo:
    return ZoneInfo(timezone_name or DEFAULT_TIMEZONE)


def to_partner_time(value: datetime, timezone_name: str | None) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Datetime must be timezone-aware.")
    return value.astimezone(get_zone(timezone_name))
