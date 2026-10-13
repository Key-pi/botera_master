from __future__ import annotations

from celery import shared_task
from django.utils import timezone

from apps.bot.delivery import deliver_scheduled_notification
from apps.notifications.services import dispatch_due_notifications, schedule_morning_digest
from apps.partners.selectors import active_partners
from core.timezones import get_zone


@shared_task(name="notifications.dispatch_due_notifications", queue="notifications")
def dispatch_due_notifications_task() -> int:
    """Dispatch due notifications through the real Telegram delivery adapter."""

    summary = dispatch_due_notifications(deliver=deliver_scheduled_notification)
    return summary.sent + summary.failed + summary.queued_for_retry + summary.skipped


@shared_task(name="notifications.schedule_morning_digests", queue="notifications")
def schedule_morning_digests_task() -> int:
    """Schedule one idempotent morning digest per active partner local day."""

    current_time = timezone.now()
    scheduled = 0
    for partner in active_partners().only("id", "name", "timezone"):
        digest_date = current_time.astimezone(get_zone(partner.timezone)).date()
        notification = schedule_morning_digest(partner=partner, digest_date=digest_date)
        if notification is not None:
            scheduled += 1
    return scheduled
