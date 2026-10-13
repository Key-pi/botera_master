from __future__ import annotations

from celery import shared_task

from apps.bot.delivery import deliver_broadcast_recipient
from apps.broadcasts.services import dispatch_due_broadcast_recipients


@shared_task(name="broadcasts.dispatch_due_recipients", queue="broadcasts")
def dispatch_due_recipients_task() -> int:
    """Dispatch queued broadcast recipients through the real Telegram delivery adapter."""

    summary = dispatch_due_broadcast_recipients(deliver=deliver_broadcast_recipient)
    return summary.sent + summary.failed + summary.queued_for_retry + summary.skipped
