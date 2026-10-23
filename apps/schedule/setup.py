from __future__ import annotations

from apps.partners.models import Partner
from apps.partners.setup import SetupStep
from apps.schedule.models import AvailabilityWindow, PartnerScheduleSettings


def completed_setup_steps_for_partner(partner: Partner) -> set[str]:
    """Report schedule-related launch readiness facts."""

    completed: set[str] = set()
    if AvailabilityWindow.objects.filter(partner=partner).exists():
        completed.add(SetupStep.AVAILABILITY_WINDOW)
    settings = PartnerScheduleSettings.objects.filter(partner=partner).first()
    if settings and settings.confirmation_mode in PartnerScheduleSettings.ConfirmationMode.values:
        completed.add(SetupStep.BOOKING_CONFIRMATION_MODE)
    return completed
