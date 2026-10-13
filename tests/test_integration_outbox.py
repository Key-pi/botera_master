import uuid
from datetime import timedelta

import pytest
from django.db import IntegrityError
from django.utils import timezone

from apps.integrations.models import IntegrationEvent
from apps.integrations.selectors import pending_events
from apps.integrations.services import (
    DEFAULT_INTEGRATION_MAX_ATTEMPTS,
    IntegrationDeliveryResult,
    dispatch_integration_event,
    dispatch_pending_events,
    record_integration_event,
)
from apps.integrations.tasks import deliver_pending_events_task


@pytest.mark.django_db
def test_record_integration_event_defaults_to_pending() -> None:
    event = record_integration_event(
        event_type="appointment.completed",
        payload={"appointment_id": "appt-1"},
    )

    assert event.status == IntegrationEvent.Status.PENDING
    assert event.source_product == "botera_master"
    assert event.payload == {"appointment_id": "appt-1"}


@pytest.mark.django_db
def test_event_id_is_unique_for_idempotency() -> None:
    event_id = uuid.uuid4()
    record_integration_event(
        event_id=event_id,
        event_type="appointment.completed",
        payload={},
    )

    with pytest.raises(IntegrityError):
        record_integration_event(
            event_id=event_id,
            event_type="appointment.completed",
            payload={},
        )


@pytest.mark.django_db
def test_pending_events_selector_excludes_terminal_statuses() -> None:
    pending = record_integration_event(event_type="appointment.completed", payload={})
    retry = record_integration_event(event_type="appointment.cancelled", payload={})
    retry.status = IntegrationEvent.Status.RETRY
    retry.save(update_fields=["status"])
    sent = record_integration_event(event_type="appointment.no_show", payload={})
    sent.status = IntegrationEvent.Status.SENT
    sent.save(update_fields=["status"])

    assert list(pending_events()) == [pending, retry]


@pytest.mark.django_db
def test_pending_events_selector_respects_retry_time() -> None:
    due_retry = record_integration_event(event_type="appointment.completed", payload={})
    due_retry.status = IntegrationEvent.Status.RETRY
    due_retry.next_retry_at = timezone.now() - timedelta(minutes=1)
    due_retry.save(update_fields=["status", "next_retry_at"])
    future_retry = record_integration_event(event_type="appointment.cancelled", payload={})
    future_retry.status = IntegrationEvent.Status.RETRY
    future_retry.next_retry_at = timezone.now() + timedelta(minutes=5)
    future_retry.save(update_fields=["status", "next_retry_at"])

    assert list(pending_events()) == [due_retry]


@pytest.mark.django_db
def test_dispatch_integration_event_success_marks_sent_and_retained() -> None:
    event = record_integration_event(
        event_type="appointment.completed",
        payload={"appointment_id": "appt-1"},
    )
    calls = []

    def deliver(prepared_event):
        calls.append(prepared_event.event_id)
        return IntegrationDeliveryResult(success=True)

    status = dispatch_integration_event(event=event, deliver=deliver)
    second_status = dispatch_integration_event(event=event, deliver=deliver)
    event.refresh_from_db()

    assert status == IntegrationEvent.Status.SENT
    assert second_status == IntegrationEvent.Status.SENT
    assert calls == [event.event_id]
    assert event.attempts == 1
    assert event.processed_at is not None
    assert event.retain_until is not None


@pytest.mark.django_db
def test_dispatch_integration_event_failure_retries_with_masked_error() -> None:
    now = timezone.now()
    event = record_integration_event(event_type="appointment.completed", payload={})

    def deliver(_event):
        return IntegrationDeliveryResult(
            success=False,
            failure_code="partnership_unavailable",
            failure_message=(
                "Token 123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi "
                "failed for +380501111111"
            ),
            retry_after_seconds=120,
        )

    status = dispatch_integration_event(event=event, deliver=deliver, now=now)
    event.refresh_from_db()

    assert status == IntegrationEvent.Status.RETRY
    assert event.attempts == 1
    assert event.next_retry_at == now + timedelta(seconds=120)
    assert event.last_error_code == "partnership_unavailable"
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in event.last_error_message_masked
    assert "+380501111111" not in event.last_error_message_masked
    assert event.processed_at is None
    assert event.retain_until is None


@pytest.mark.django_db
def test_dispatch_integration_event_terminal_failure_marks_failed() -> None:
    event = record_integration_event(event_type="appointment.completed", payload={})

    def deliver(_event):
        return IntegrationDeliveryResult(
            success=False,
            failure_code="bad_request",
            failure_message="Invalid event schema",
            terminal=True,
        )

    status = dispatch_integration_event(event=event, deliver=deliver)
    event.refresh_from_db()

    assert status == IntegrationEvent.Status.FAILED
    assert event.attempts == 1
    assert event.processed_at is not None
    assert event.retain_until is not None


@pytest.mark.django_db
def test_dispatch_integration_event_fails_after_max_attempts() -> None:
    event = record_integration_event(event_type="appointment.completed", payload={})
    event.attempts = DEFAULT_INTEGRATION_MAX_ATTEMPTS - 1
    event.save(update_fields=["attempts"])

    def deliver(_event):
        return IntegrationDeliveryResult(
            success=False,
            failure_code="timeout",
            failure_message="Partnership timeout",
        )

    status = dispatch_integration_event(event=event, deliver=deliver)
    event.refresh_from_db()

    assert status == IntegrationEvent.Status.FAILED
    assert event.attempts == DEFAULT_INTEGRATION_MAX_ATTEMPTS


@pytest.mark.django_db
def test_dispatch_pending_events_summarizes_due_results() -> None:
    first = record_integration_event(event_type="appointment.completed", payload={})
    second = record_integration_event(event_type="appointment.cancelled", payload={})
    second.status = IntegrationEvent.Status.RETRY
    second.next_retry_at = timezone.now() - timedelta(minutes=1)
    second.save(update_fields=["status", "next_retry_at"])

    def deliver(event):
        return IntegrationDeliveryResult(success=event.id == first.id)

    summary = dispatch_pending_events(deliver=deliver)

    assert summary.sent == 1
    assert summary.queued_for_retry == 1
    assert summary.failed == 0


@pytest.mark.django_db
def test_deliver_pending_events_task_is_disabled_until_target_exists(settings) -> None:
    event = record_integration_event(event_type="appointment.completed", payload={})
    settings.INTEGRATION_EVENT_DELIVERY_ENABLED = False

    processed = deliver_pending_events_task()
    event.refresh_from_db()

    assert processed == 0
    assert event.status == IntegrationEvent.Status.PENDING
