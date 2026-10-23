from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from django.db.models import QuerySet
from django.utils import timezone

from apps.notifications.models import ReminderRule, ScheduledNotification

if TYPE_CHECKING:
    from datetime import datetime

    from apps.partners.models import Partner


def active_reminder_rules_for_partner(
    *,
    partner: Partner,
    target: str | None = None,
) -> QuerySet[ReminderRule]:
    """Return active reminder rules for a partner and optional target."""

    queryset = ReminderRule.objects.filter(partner=partner, is_active=True)
    if target is not None:
        queryset = queryset.filter(target=target)
    return queryset.order_by("-offset_minutes")


def due_notifications(
    *,
    now: datetime | None = None,
) -> QuerySet[ScheduledNotification]:
    """Return queued notifications ready for dispatch."""

    current_time = now or timezone.now()
    return (
        ScheduledNotification.objects.filter(
            status=ScheduledNotification.Status.QUEUED,
            due_at__lte=current_time,
        )
        .select_related(
            "partner",
            "appointment",
            "client_profile",
            "penalty",
            "penalty__source_appointment",
            "reminder_rule",
            "source_notification",
        )
        .order_by("due_at", "created_at")
    )


def failed_notifications_visible_to_partner(partner: Partner) -> QuerySet[ScheduledNotification]:
    """Return failed delivery markers that need partner attention."""

    return (
        ScheduledNotification.objects.filter(
            partner=partner,
            status=ScheduledNotification.Status.FAILED,
            failed_visible_to_partner=True,
        )
        .select_related("appointment", "appointment__client_profile", "client_profile")
        .order_by("-updated_at")
    )


def notification_for_partner_by_id(
    *,
    partner: Partner,
    notification_id: UUID,
) -> ScheduledNotification | None:
    """Resolve one notification and its callback dependencies inside one tenant."""

    return (
        ScheduledNotification.objects.filter(
            partner=partner,
            id=notification_id,
        )
        .select_related(
            "partner",
            "appointment",
            "client_profile",
            "client_profile__telegram_identity",
            "penalty",
            "penalty__source_appointment",
            "source_notification",
        )
        .first()
    )
