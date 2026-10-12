from __future__ import annotations

from django.db import models

from core.database.models import TimeStampedModel


class BotInstance(TimeStampedModel):
    """Technical Telegram bot connection owned by one partner."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        SUSPENDED = "suspended", "Suspended"
        ERROR = "error", "Error"

    class DeliveryMode(models.TextChoices):
        POLLING = "polling", "Polling"

    partner = models.OneToOneField(
        "partners.Partner",
        on_delete=models.CASCADE,
        related_name="bot_instance",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PAUSED)
    delivery_mode = models.CharField(
        max_length=16,
        choices=DeliveryMode.choices,
        default=DeliveryMode.POLLING,
    )
    encrypted_token = models.TextField(blank=True)
    token_key_id = models.CharField(max_length=64, blank=True)
    token_fingerprint = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        unique=True,
        db_index=True,
    )
    token_masked = models.CharField(max_length=64, blank=True)
    telegram_bot_id = models.BigIntegerField(null=True, blank=True)
    telegram_username = models.CharField(max_length=255, blank=True)
    last_error_code = models.CharField(max_length=128, blank=True)
    last_error_message_masked = models.TextField(blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_started_at = models.DateTimeField(null=True, blank=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["partner__name"]
        indexes = [
            models.Index(fields=["status", "delivery_mode"]),
            models.Index(fields=["partner", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.partner} bot ({self.status})"

    @property
    def has_token(self) -> bool:
        return bool(self.encrypted_token and self.token_fingerprint)
