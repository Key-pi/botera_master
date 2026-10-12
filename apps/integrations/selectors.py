from django.db.models import QuerySet

from apps.integrations.models import IntegrationEvent


def pending_events() -> QuerySet[IntegrationEvent]:
    return IntegrationEvent.objects.filter(
        status__in=[IntegrationEvent.Status.PENDING, IntegrationEvent.Status.RETRY]
    ).order_by("created_at")
