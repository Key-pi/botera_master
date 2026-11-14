from __future__ import annotations

from celery import shared_task

from apps.bot.delivery import deliver_broadcast_recipient
from apps.broadcasts.services import (
    dispatch_due_broadcast_recipients,
    queue_due_scheduled_broadcast_campaigns,
)


@shared_task(name="broadcasts.queue_due_campaigns")
def queue_due_campaigns_task(limit: int = 100) -> int:
    """Materialize recipients for scheduled campaigns whose time has arrived."""

    return queue_due_scheduled_broadcast_campaigns(limit=max(1, limit))


@shared_task(name="broadcasts.dispatch_due_recipients")
def dispatch_due_recipients_task(limit: int = 1) -> int:
    """Dispatch a bounded batch through the real Telegram delivery adapter.

    Beat invokes this task once per second with the conservative pilot default
    of one recipient. Durable recipient state keeps subsequent invocations
    resumable, while avoiding an accidental Telegram burst when the scheduler
    is first enabled. A future per-bot rate gate can raise this batch size
    without changing campaign or recipient semantics.
    """

    summary = dispatch_due_broadcast_recipients(
        deliver=deliver_broadcast_recipient,
        limit=max(1, limit),
    )
    return summary.sent + summary.failed + summary.queued_for_retry + summary.skipped
