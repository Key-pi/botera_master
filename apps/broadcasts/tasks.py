from __future__ import annotations

from celery import shared_task

from apps.bot.delivery import deliver_broadcast_recipient
from apps.broadcasts.services import dispatch_due_broadcast_recipients


@shared_task(name="broadcasts.dispatch_due_recipients", queue="broadcasts")
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
