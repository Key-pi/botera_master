from __future__ import annotations

from celery import shared_task


@shared_task(name="broadcasts.dispatch_due_recipients", queue="broadcasts")
def dispatch_due_recipients_task() -> int:
    """Broadcast worker placeholder until the Telegram send adapter is wired.

    The durable broadcast state machine is implemented and tested in
    `apps.broadcasts.services`. This task stays intentionally inert until the
    bot transport/content adapter can be connected without faking delivery.
    """

    return 0
