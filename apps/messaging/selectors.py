from __future__ import annotations

from uuid import UUID

from django.db.models import QuerySet

from apps.clients.models import ClientProfile
from apps.messaging.models import ConversationMessage
from apps.partners.models import Partner


def messages_for_partner(partner: Partner) -> QuerySet[ConversationMessage]:
    """Return partner-scoped conversation messages with common relations."""

    return ConversationMessage.objects.filter(partner=partner).select_related(
        "partner",
        "client_profile",
        "created_by",
    )


def messages_for_client(client_profile: ClientProfile) -> QuerySet[ConversationMessage]:
    """Return all bot-mediated conversation history for one partner client."""

    return messages_for_partner(client_profile.partner).filter(client_profile=client_profile)


def conversation_message_for_partner_by_id(
    *,
    partner: Partner,
    message_id: UUID,
) -> ConversationMessage | None:
    """Resolve one conversation message without crossing the partner boundary."""

    return messages_for_partner(partner).filter(id=message_id).first()


def queued_conversation_messages() -> QuerySet[ConversationMessage]:
    """Return queued messages for future delivery workers."""

    return (
        ConversationMessage.objects.filter(
            delivery_status=ConversationMessage.DeliveryStatus.QUEUED,
        )
        .select_related(
            "partner",
            "client_profile",
            "client_profile__telegram_identity",
        )
        .order_by("created_at")
    )
