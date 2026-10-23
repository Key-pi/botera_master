from django.db import models

from core.database.models import RetainUntilModel, TimeStampedModel


class IntegrationEvent(TimeStampedModel, RetainUntilModel):
    """Transactional outbox event produced by BoteraMaster domain services."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"
        RETRY = "retry", "Retry"
        FAILED = "failed", "Failed"

    event_id = models.UUIDField(unique=True, db_index=True)
    event_type = models.CharField(max_length=128, db_index=True)
    source_product = models.CharField(max_length=64, default="botera_master")
    source_business_id = models.UUIDField(null=True, blank=True, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error_code = models.CharField(max_length=128, blank=True)
    last_error_message_masked = models.TextField(blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["status", "next_retry_at"]),
            models.Index(fields=["source_product", "event_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.event_type}:{self.event_id}"
