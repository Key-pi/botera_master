from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.integrations.models import IntegrationEvent


def pending_events(*, now=None) -> QuerySet[IntegrationEvent]:
    """Return outbox events that are ready for a delivery attempt."""

    current_time = now or timezone.now()
    return IntegrationEvent.objects.filter(
        Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=current_time),
        status__in=[IntegrationEvent.Status.PENDING, IntegrationEvent.Status.RETRY],
    ).order_by("created_at")
