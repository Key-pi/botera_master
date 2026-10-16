from __future__ import annotations

from celery import shared_task

from apps.appointments.proposals import expire_pending_appointment_proposals


@shared_task(name="appointments.expire_pending_proposals", queue="maintenance")
def expire_pending_appointment_proposals_task() -> int:
    """Expire due proposals in bounded batches; later runs resume remaining rows."""

    return expire_pending_appointment_proposals()
