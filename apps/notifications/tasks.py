from __future__ import annotations

from celery import shared_task


@shared_task(name="notifications.dispatch_due_notifications")
def dispatch_due_notifications_task() -> int:
    """Celery dispatcher placeholder until the Telegram delivery adapter exists.

    The durable dispatch state machine is implemented in
    `apps.notifications.services.dispatch_due_notifications` and is tested with
    injected delivery backends. This task intentionally avoids pretending to send
    Telegram messages before the bot content/transport layer is wired.
    """

    return 0


@shared_task(name="notifications.schedule_morning_digests")
def schedule_morning_digests_task() -> int:
    """Placeholder beat entrypoint for the morning digest scheduler."""

    return 0
