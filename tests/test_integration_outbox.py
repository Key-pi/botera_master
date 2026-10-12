import uuid

import pytest
from django.db import IntegrityError

from apps.integrations.models import IntegrationEvent
from apps.integrations.selectors import pending_events
from apps.integrations.services import record_integration_event


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
