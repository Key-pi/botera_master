from django.db import models
from django.utils.translation import gettext_lazy as _

from core.database.models import RetainUntilModel, TimeStampedModel


class IntegrationEvent(TimeStampedModel, RetainUntilModel):
    """Transactional outbox event produced by BoteraMaster domain services."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        SENDING = "sending", _("Sending")
        SENT = "sent", _("Sent")
        RETRY = "retry", _("Retry")
        FAILED = "failed", _("Failed")

    event_id = models.UUIDField(_("event ID"), unique=True, db_index=True)
    event_type = models.CharField(_("event type"), max_length=128, db_index=True)
    source_product = models.CharField(_("source product"), max_length=64, default="botera_master")
    source_business_id = models.UUIDField(
        _("source business ID"), null=True, blank=True, db_index=True
    )
    payload = models.JSONField(_("payload"), default=dict, blank=True)
    status = models.CharField(
        _("status"), max_length=16, choices=Status.choices, default=Status.PENDING
    )
    attempts = models.PositiveSmallIntegerField(_("attempts"), default=0)
    last_error_code = models.CharField(_("last error code"), max_length=128, blank=True)
    last_error_message_masked = models.TextField(_("masked last error message"), blank=True)
    next_retry_at = models.DateTimeField(_("next retry at"), null=True, blank=True, db_index=True)
    processed_at = models.DateTimeField(_("processed at"), null=True, blank=True)

    class Meta:
        verbose_name = _("integration event")
        verbose_name_plural = _("integration events")
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["status", "next_retry_at"]),
            models.Index(fields=["source_product", "event_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.event_type}:{self.event_id}"
