from celery import shared_task


@shared_task(name="integrations.deliver_pending_events")
def deliver_pending_events() -> int:
    """Placeholder delivery task.

    Concrete Partnership delivery is implemented after the local outbox contract
    is stable and a target service exists.
    """

    return 0
