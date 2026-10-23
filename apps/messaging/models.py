from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from core.database.models import PartnerBoundModel


class ConversationMessage(PartnerBoundModel):
    """Partner-scoped client-master message routed through the partner bot."""

    class Direction(models.TextChoices):
        CLIENT_TO_MASTER = "client_to_master", "Client to master"
        MASTER_TO_CLIENT = "master_to_client", "Master to client"

    class DeliveryStatus(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    client_profile = models.ForeignKey(
        "clients.ClientProfile",
        on_delete=models.CASCADE,
        related_name="conversation_messages",
    )
    direction = models.CharField(max_length=32, choices=Direction.choices)
    text = models.TextField()
    delivery_status = models.CharField(
        max_length=16,
        choices=DeliveryStatus.choices,
        default=DeliveryStatus.QUEUED,
    )
    failure_code = models.CharField(max_length=128, blank=True)
    failure_message_masked = models.TextField(blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_conversation_messages",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["partner", "client_profile", "-created_at"]),
            models.Index(fields=["partner", "delivery_status", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.client_profile} {self.direction} ({self.delivery_status})"

    def clean(self) -> None:
        super().clean()
        if (
            self.client_profile_id
            and self.partner_id
            and self.client_profile.partner_id != self.partner_id
        ):
            raise ValidationError(
                {"client_profile": "Message client must belong to the same partner."}
            )
        if not self.text.strip():
            raise ValidationError({"text": "Message text is required."})
