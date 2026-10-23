from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import models

from apps.partners.models import Partner


class SetupStep(models.TextChoices):
    BOT_TOKEN = "bot_token", "Bot token"
    LANGUAGE = "language", "Language"
    TIMEZONE = "timezone", "Timezone"
    CATALOG_SERVICE = "catalog_service", "Catalog service"
    AVAILABILITY_WINDOW = "availability_window", "Availability window"
    BOOKING_CONFIRMATION_MODE = "booking_confirmation_mode", "Booking confirmation mode"


EXTERNAL_SETUP_STEPS = (
    SetupStep.BOT_TOKEN,
    SetupStep.CATALOG_SERVICE,
    SetupStep.AVAILABILITY_WINDOW,
    SetupStep.BOOKING_CONFIRMATION_MODE,
)

SetupChecker = Callable[[Partner], set[str]]
_SETUP_CHECKERS: dict[str, SetupChecker] = {}


@dataclass(frozen=True)
class PartnerSetupStatus:
    partner: Partner
    missing_steps: tuple[str, ...]

    @property
    def is_complete(self) -> bool:
        return not self.missing_steps


def get_partner_setup_status(
    partner: Partner,
    *,
    completed_external_steps: set[str] | None = None,
) -> PartnerSetupStatus:
    """Evaluate minimum launch setup with extension points for future apps.

    Bot, catalog, and schedule models arrive in later milestones. Until those
    apps can report their own facts, their required launch steps remain missing.
    """

    completed_external_steps = _completed_registered_steps(partner) | (
        completed_external_steps or set()
    )
    missing_steps: list[str] = []

    if not partner.default_language:
        missing_steps.append(SetupStep.LANGUAGE)

    if not _has_valid_timezone(partner.timezone):
        missing_steps.append(SetupStep.TIMEZONE)

    for step in EXTERNAL_SETUP_STEPS:
        if step not in completed_external_steps:
            missing_steps.append(step)

    return PartnerSetupStatus(partner=partner, missing_steps=tuple(missing_steps))


def register_setup_checker(name: str, checker: SetupChecker) -> None:
    """Register an app-owned setup checker by stable app key."""

    _SETUP_CHECKERS[name] = checker


def _completed_registered_steps(partner: Partner) -> set[str]:
    completed: set[str] = set()
    for checker in _SETUP_CHECKERS.values():
        completed.update(checker(partner))
    return completed


def is_partner_runtime_eligible(
    partner: Partner,
    *,
    completed_external_steps: set[str] | None = None,
) -> bool:
    """Return whether client-facing bot flows may run for this partner."""

    if partner.status != Partner.Status.ACTIVE:
        return False

    return get_partner_setup_status(
        partner,
        completed_external_steps=completed_external_steps,
    ).is_complete


def _has_valid_timezone(timezone_name: str) -> bool:
    if not timezone_name:
        return False
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return False
    return True
