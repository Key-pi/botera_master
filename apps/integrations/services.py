from __future__ import annotations

import uuid
from typing import Any

from apps.integrations.models import IntegrationEvent


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
