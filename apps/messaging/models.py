from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.database.models import PartnerBoundModel


class ConversationMessage(PartnerBoundModel):
    """Partner-scoped client-master message routed through the partner bot."""

    class Direction(models.TextChoices):
        CLIENT_TO_MASTER = "client_to_master", _("Client to master")
        MASTER_TO_CLIENT = "master_to_client", _("Master to client")

    class DeliveryStatus(models.TextChoices):
        QUEUED = "queued", _("Queued")
        SENDING = "sending", _("Sending")
        SENT = "sent", _("Sent")
        FAILED = "failed", _("Failed")
        SKIPPED = "skipped", _("Skipped")

    client_profile = models.ForeignKey(
        "clients.ClientProfile",
        on_delete=models.CASCADE,
        related_name="conversation_messages",
        verbose_name=_("client profile"),
    )
    direction = models.CharField(_("direction"), max_length=32, choices=Direction.choices)
    text = models.TextField(_("text"))
    delivery_status = models.CharField(
        _("delivery status"),
        max_length=16,
        choices=DeliveryStatus.choices,
        default=DeliveryStatus.QUEUED,
    )
    failure_code = models.CharField(_("failure code"), max_length=128, blank=True)
    failure_message_masked = models.TextField(_("masked failure message"), blank=True)
    delivered_at = models.DateTimeField(_("delivered at"), null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_conversation_messages",
        null=True,
        blank=True,
        verbose_name=_("created by"),
    )

    class Meta:
        verbose_name = _("conversation message")
        verbose_name_plural = _("conversation messages")
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
