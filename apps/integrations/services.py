from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.integrations.models import IntegrationEvent
from apps.integrations.selectors import pending_events
from core.security.masking import mask_sensitive_text

INTEGRATION_EVENT_RETAIN_DAYS = 90
DEFAULT_INTEGRATION_MAX_ATTEMPTS = 5
INTEGRATION_RETRY_BACKOFFS = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(hours=1),
)


@dataclass(frozen=True, slots=True)
class IntegrationDeliveryResult:
    """Adapter-neutral result returned by a future external integration client."""

    success: bool
    failure_code: str = ""
    failure_message: str = ""
    terminal: bool = False
    retry_after_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class IntegrationDispatchSummary:
    """Counts from one outbox dispatch pass."""

    sent: int = 0
    failed: int = 0
    queued_for_retry: int = 0
    ignored: int = 0

    def add(self, status: str) -> IntegrationDispatchSummary:
        if status == IntegrationEvent.Status.SENT:
            return IntegrationDispatchSummary(
                sent=self.sent + 1,
                failed=self.failed,
                queued_for_retry=self.queued_for_retry,
                ignored=self.ignored,
            )
        if status == IntegrationEvent.Status.FAILED:
            return IntegrationDispatchSummary(
                sent=self.sent,
                failed=self.failed + 1,
                queued_for_retry=self.queued_for_retry,
                ignored=self.ignored,
            )
        if status == IntegrationEvent.Status.RETRY:
            return IntegrationDispatchSummary(
                sent=self.sent,
                failed=self.failed,
                queued_for_retry=self.queued_for_retry + 1,
                ignored=self.ignored,
            )
        return IntegrationDispatchSummary(
            sent=self.sent,
            failed=self.failed,
            queued_for_retry=self.queued_for_retry,
            ignored=self.ignored + 1,
        )


DeliverIntegrationEvent = Callable[[IntegrationEvent], IntegrationDeliveryResult]


def record_integration_event(
    *,
    event_type: str,
    payload: dict[str, Any],
    source_business_id: uuid.UUID | None = None,
    event_id: uuid.UUID | None = None,
) -> IntegrationEvent:
    """Record a local outbox event without performing network I/O."""

    return IntegrationEvent.objects.create(
        event_id=event_id or uuid.uuid4(),
        event_type=event_type,
        source_business_id=source_business_id,
        payload=payload,
    )


def dispatch_pending_events(
    *,
    deliver: DeliverIntegrationEvent,
    now=None,
    limit: int = 100,
) -> IntegrationDispatchSummary:
    """Dispatch due integration outbox events without coupling to a target product."""

    current_time = now or timezone.now()
    summary = IntegrationDispatchSummary()
    event_ids = list(pending_events(now=current_time).values_list("id", flat=True)[:limit])
    for event_id in event_ids:
        event = IntegrationEvent.objects.get(id=event_id)
        status = dispatch_integration_event(
            event=event,
            deliver=deliver,
            now=current_time,
        )
        summary = summary.add(status)
    return summary


def dispatch_integration_event(
    *,
    event: IntegrationEvent,
    deliver: DeliverIntegrationEvent,
    now=None,
) -> str:
    """Dispatch one outbox event with durable retry/idempotency state."""

    current_time = now or timezone.now()
    prepared = _prepare_event_for_delivery(event=event, now=current_time)
    if prepared is None:
        event.refresh_from_db(fields=["status"])
        return event.status

    try:
        result = deliver(prepared)
    except Exception as exc:
        result = IntegrationDeliveryResult(
            success=False,
            failure_code="integration_delivery_exception",
            failure_message=str(exc),
        )
    return _record_integration_delivery_result(
        event=prepared,
        result=result,
        now=current_time,
    )


def _prepare_event_for_delivery(*, event: IntegrationEvent, now) -> IntegrationEvent | None:
    with transaction.atomic():
        locked = IntegrationEvent.objects.select_for_update().get(id=event.id)
        if locked.status not in {
            IntegrationEvent.Status.PENDING,
            IntegrationEvent.Status.RETRY,
        }:
            return None
        if locked.next_retry_at and locked.next_retry_at > now:
            return None
        locked.status = IntegrationEvent.Status.SENDING
        locked.save(update_fields=["status", "updated_at"])
        return locked


def _record_integration_delivery_result(
    *,
    event: IntegrationEvent,
    result: IntegrationDeliveryResult,
    now,
) -> str:
    with transaction.atomic():
        locked = IntegrationEvent.objects.select_for_update().get(id=event.id)
        if locked.status != IntegrationEvent.Status.SENDING:
            return locked.status

        locked.attempts += 1
        if result.success:
            locked.status = IntegrationEvent.Status.SENT
            locked.last_error_code = ""
            locked.last_error_message_masked = ""
            locked.next_retry_at = None
            locked.processed_at = now
            locked.retain_until = now + timedelta(days=INTEGRATION_EVENT_RETAIN_DAYS)
        else:
            _apply_integration_delivery_failure(event=locked, result=result, now=now)

        locked.save(
            update_fields=[
                "status",
                "attempts",
                "last_error_code",
                "last_error_message_masked",
                "next_retry_at",
                "processed_at",
                "retain_until",
                "updated_at",
            ]
        )
        return locked.status


def _apply_integration_delivery_failure(
    *,
    event: IntegrationEvent,
    result: IntegrationDeliveryResult,
    now,
) -> None:
    event.last_error_code = (result.failure_code or "integration_delivery_failed")[:128]
    event.last_error_message_masked = mask_sensitive_text(result.failure_message)

    if result.terminal or event.attempts >= DEFAULT_INTEGRATION_MAX_ATTEMPTS:
        event.status = IntegrationEvent.Status.FAILED
        event.processed_at = now
        event.next_retry_at = None
        event.retain_until = now + timedelta(days=INTEGRATION_EVENT_RETAIN_DAYS)
        return

    event.status = IntegrationEvent.Status.RETRY
    event.next_retry_at = _next_retry_at(
        attempts=event.attempts,
        now=now,
        retry_after_seconds=result.retry_after_seconds,
    )


def _next_retry_at(*, attempts: int, now, retry_after_seconds: int | None):
    if retry_after_seconds is not None and retry_after_seconds > 0:
        return now + timedelta(seconds=retry_after_seconds)
    backoff = INTEGRATION_RETRY_BACKOFFS[
        min(attempts - 1, len(INTEGRATION_RETRY_BACKOFFS) - 1)
    ]
    return now + backoff
