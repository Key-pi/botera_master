from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from datetime import timezone as datetime_timezone
from typing import TYPE_CHECKING

from django.db.models import QuerySet
from django.utils import timezone

from apps.accounts.models import PartnerMembership
from apps.appointments.models import Appointment
from apps.appointments.selectors import (
    appointments_for_partner_period,
    requested_appointments_for_partner,
)
from apps.bot.models import BotInstance
from apps.bot.selectors import bot_instance_for_partner
from apps.finance.selectors import FinanceReport, finance_report_for_partner
from apps.notifications.models import ScheduledNotification
from apps.notifications.selectors import failed_notifications_visible_to_partner
from apps.partners.models import Partner
from apps.partners.setup import PartnerSetupStatus, SetupStep, get_partner_setup_status
from core.timezones import get_zone

if TYPE_CHECKING:
    from apps.accounts.models import User


DASHBOARD_LIST_LIMIT = 6
DASHBOARD_APPOINTMENT_STATUSES = (
    Appointment.Status.REQUESTED,
    Appointment.Status.CONFIRMED,
    Appointment.Status.COMPLETED,
    Appointment.Status.NO_SHOW,
)


@dataclass(frozen=True, slots=True)
class PartnerDashboardSnapshot:
    """Tenant-scoped operational summary for the partner landing screen."""

    partner: Partner
    setup_status: PartnerSetupStatus
    missing_setup_labels: tuple[str, ...]
    bot_instance: BotInstance | None
    today: date
    tomorrow: date
    today_appointments: tuple[Appointment, ...]
    today_appointment_count: int
    tomorrow_appointments: tuple[Appointment, ...]
    tomorrow_appointment_count: int
    pending_requests: tuple[Appointment, ...]
    pending_request_count: int
    failed_notifications: tuple[ScheduledNotification, ...]
    failed_notification_count: int
    today_finance: FinanceReport


def visible_partners_for_user(user: User) -> QuerySet[Partner]:
    """Return partners visible to a platform or partner-admin user."""

    queryset = Partner.objects.all()
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return queryset.none()
    if user.is_superuser:
        return queryset

    return queryset.filter(
        memberships__user=user,
        memberships__role=PartnerMembership.Role.OWNER,
        memberships__is_active=True,
    ).distinct()


def partner_dashboard_snapshot(
    *,
    partner: Partner,
    now: datetime | None = None,
) -> PartnerDashboardSnapshot:
    """Compose existing owner reads into one bounded partner dashboard snapshot."""

    current_time = now or timezone.now()
    zone = get_zone(partner.timezone)
    local_today = current_time.astimezone(zone).date()
    local_tomorrow = local_today + timedelta(days=1)
    period_start = _local_midnight_utc(day=local_today, zone=zone)
    period_end = _local_midnight_utc(day=local_tomorrow + timedelta(days=1), zone=zone)

    period_appointments = tuple(
        appointments_for_partner_period(
            partner=partner,
            start_at=period_start,
            end_at=period_end,
            statuses=DASHBOARD_APPOINTMENT_STATUSES,
        )
    )
    today_appointments = tuple(
        appointment
        for appointment in period_appointments
        if appointment.start_at.astimezone(zone).date() == local_today
    )
    tomorrow_appointments = tuple(
        appointment
        for appointment in period_appointments
        if appointment.start_at.astimezone(zone).date() == local_tomorrow
    )

    pending_queryset = requested_appointments_for_partner(partner=partner)
    failed_queryset = failed_notifications_visible_to_partner(partner)
    setup_status = get_partner_setup_status(partner)

    return PartnerDashboardSnapshot(
        partner=partner,
        setup_status=setup_status,
        missing_setup_labels=tuple(_setup_step_label(step) for step in setup_status.missing_steps),
        bot_instance=bot_instance_for_partner(partner),
        today=local_today,
        tomorrow=local_tomorrow,
        today_appointments=today_appointments[:DASHBOARD_LIST_LIMIT],
        today_appointment_count=len(today_appointments),
        tomorrow_appointments=tomorrow_appointments[:DASHBOARD_LIST_LIMIT],
        tomorrow_appointment_count=len(tomorrow_appointments),
        pending_requests=tuple(pending_queryset[:DASHBOARD_LIST_LIMIT]),
        pending_request_count=pending_queryset.count(),
        failed_notifications=tuple(failed_queryset[:DASHBOARD_LIST_LIMIT]),
        failed_notification_count=failed_queryset.count(),
        today_finance=finance_report_for_partner(
            partner=partner,
            start_date=local_today,
            end_date=local_today,
            now=current_time,
        ),
    )


def setup_required_partners() -> QuerySet[Partner]:
    """Partners that are still in onboarding/setup state."""

    return Partner.objects.filter(status=Partner.Status.SETUP_REQUIRED)


def active_partners() -> QuerySet[Partner]:
    """Partners whose runtime-facing background jobs may be scheduled."""

    return Partner.objects.filter(status=Partner.Status.ACTIVE)


def partners_with_missing_setup() -> list[tuple[Partner, tuple[str, ...]]]:
    """Small superuser helper for setup/problem views."""

    result: list[tuple[Partner, tuple[str, ...]]] = []
    for partner in setup_required_partners():
        status = get_partner_setup_status(partner)
        result.append((partner, status.missing_steps))
    return result


def _local_midnight_utc(*, day: date, zone) -> datetime:
    return datetime.combine(day, time.min, tzinfo=zone).astimezone(datetime_timezone.utc)


def _setup_step_label(step: str) -> str:
    try:
        return SetupStep(step).label
    except ValueError:
        return step.replace("_", " ").capitalize()
