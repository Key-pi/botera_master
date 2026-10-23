from __future__ import annotations

from celery import shared_task
from django.conf import settings

from apps.integrations.services import (
    IntegrationDeliveryResult,
    dispatch_pending_events,
)


@shared_task(name="integrations.deliver_pending_events", queue="default")
def deliver_pending_events_task() -> int:
    """Dispatch integration events only when an external target is configured.

    Partnership/Discovery are separate products, so the MVP default is to leave
    delivery disabled rather than pretending events were sent somewhere.
    """

    if not settings.INTEGRATION_EVENT_DELIVERY_ENABLED:
        return 0
    summary = dispatch_pending_events(deliver=_delivery_target_not_configured)
    return summary.sent + summary.failed + summary.queued_for_retry


def _delivery_target_not_configured(_event) -> IntegrationDeliveryResult:
    """Fail closed if delivery was enabled before a real adapter was added."""

    return IntegrationDeliveryResult(
        success=False,
        failure_code="integration_target_not_configured",
        failure_message="Integration delivery is enabled but no target adapter is configured.",
        terminal=True,
    )


def deliver_pending_events() -> int:
    """Backward-compatible alias for older local imports."""

    return deliver_pending_events_task()
