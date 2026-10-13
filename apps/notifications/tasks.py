from __future__ import annotations

from celery import shared_task

from apps.bot.delivery import deliver_scheduled_notification
from apps.notifications.services import dispatch_due_notifications


@shared_task(name="notifications.dispatch_due_notifications")
def dispatch_due_notifications_task() -> int:
    """Dispatch due notifications through the real Telegram delivery adapter."""

    summary = dispatch_due_notifications(deliver=deliver_scheduled_notification)
    return summary.sent + summary.failed + summary.queued_for_retry + summary.skipped


@shared_task(name="notifications.schedule_morning_digests")
def schedule_morning_digests_task() -> int:
    """Placeholder beat entrypoint for the morning digest scheduler."""

    return 0
