from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.database.models import PartnerBoundModel, RetainUntilModel

BROADCAST_RECIPIENT_RETAIN_DAYS = 90
DEFAULT_BROADCAST_MAX_ATTEMPTS = 3
MIN_BROADCAST_SEND_INTERVAL_SECONDS = 0.5


class BroadcastCampaign(PartnerBoundModel):
    """Partner-created marketing broadcast campaign."""

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        SCHEDULED = "scheduled", _("Scheduled")
        QUEUED = "queued", _("Queued")
        SENDING = "sending", _("Sending")
        COMPLETED = "completed", _("Completed")
        CANCELLED = "cancelled", _("Cancelled")
        FAILED_PARTIAL = "failed_partial", _("Partially failed")

    text = models.TextField(_("text"), blank=True)
    photo = models.CharField(_("photo"), max_length=512, blank=True)
    buttons = models.JSONField(_("buttons"), default=list, blank=True)
    filters = models.JSONField(_("filters"), default=dict, blank=True)
    status = models.CharField(
        _("status"), max_length=32, choices=Status.choices, default=Status.DRAFT
    )
    scheduled_at = models.DateTimeField(_("scheduled at"), null=True, blank=True, db_index=True)
    queued_count = models.PositiveIntegerField(_("queued count"), default=0)
    sent_count = models.PositiveIntegerField(_("sent count"), default=0)
    failed_count = models.PositiveIntegerField(_("failed count"), default=0)
    skipped_count = models.PositiveIntegerField(_("skipped count"), default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_broadcast_campaigns",
        null=True,
        blank=True,
        verbose_name=_("created by"),
    )
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    finished_at = models.DateTimeField(_("finished at"), null=True, blank=True)

    class Meta:
        verbose_name = _("broadcast campaign")
        verbose_name_plural = _("broadcast campaigns")
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(text__gt="") | models.Q(photo__gt="")),
                name="broadcast_campaign_has_text_or_photo",
            ),
            models.CheckConstraint(
                condition=models.Q(queued_count__gte=0)
                & models.Q(sent_count__gte=0)
                & models.Q(failed_count__gte=0)
                & models.Q(skipped_count__gte=0),
                name="broadcast_campaign_counters_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["partner", "status", "created_at"]),
            models.Index(fields=["status", "scheduled_at"]),
        ]

    def __str__(self) -> str:
        preview = self.text[:40] if self.text else self.photo[:40]
        return f"{self.partner} broadcast {preview}"


class BroadcastRecipient(PartnerBoundModel, RetainUntilModel):
    """Per-client delivery state for one broadcast campaign."""

    class Status(models.TextChoices):
        QUEUED = "queued", _("Queued")
        SENDING = "sending", _("Sending")
        SENT = "sent", _("Sent")
        FAILED = "failed", _("Failed")
        SKIPPED = "skipped", _("Skipped")

    campaign = models.ForeignKey(
        "broadcasts.BroadcastCampaign",
        on_delete=models.CASCADE,
        related_name="recipients",
        verbose_name=_("campaign"),
    )
    client_profile = models.ForeignKey(
        "clients.ClientProfile",
        on_delete=models.CASCADE,
        related_name="broadcast_recipients",
        verbose_name=_("client profile"),
    )
    status = models.CharField(
        _("status"), max_length=16, choices=Status.choices, default=Status.QUEUED
    )
    attempt_count = models.PositiveSmallIntegerField(_("attempt count"), default=0)
    max_attempts = models.PositiveSmallIntegerField(
        _("maximum attempts"), default=DEFAULT_BROADCAST_MAX_ATTEMPTS
    )
    next_attempt_at = models.DateTimeField(
        _("next attempt at"), null=True, blank=True, db_index=True
    )
    sent_at = models.DateTimeField(_("sent at"), null=True, blank=True)
    failure_code = models.CharField(_("failure code"), max_length=128, blank=True)
    failure_message_masked = models.TextField(_("masked failure message"), blank=True)

    class Meta:
        verbose_name = _("broadcast recipient")
        verbose_name_plural = _("broadcast recipients")
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "client_profile"],
                name="broadcast_unique_campaign_client",
            ),
        ]
        indexes = [
            models.Index(fields=["campaign", "status", "next_attempt_at"]),
            models.Index(fields=["partner", "status", "next_attempt_at"]),
            models.Index(fields=["client_profile", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.campaign_id}:{self.client_profile_id}:{self.status}"

    def clean(self) -> None:
        super().clean()
        if self.campaign_id and self.partner_id and self.campaign.partner_id != self.partner_id:
            raise ValidationError({"campaign": "Campaign must belong to the same partner."})
        if (
            self.client_profile_id
            and self.partner_id
            and self.client_profile.partner_id != self.partner_id
        ):
            raise ValidationError(
                {"client_profile": "Recipient client must belong to the same partner."}
            )


class BroadcastTemplate(PartnerBoundModel):
    """Reusable partner-authored broadcast draft."""

    name = models.CharField(_("name"), max_length=255)
    text = models.TextField(_("text"), blank=True)
    photo = models.CharField(_("photo"), max_length=512, blank=True)
    buttons = models.JSONField(_("buttons"), default=list, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_broadcast_templates",
        null=True,
        blank=True,
        verbose_name=_("created by"),
    )

    class Meta:
        verbose_name = _("broadcast template")
        verbose_name_plural = _("broadcast templates")
        ordering = ["partner__name", "name"]
        indexes = [
            models.Index(fields=["partner", "name"]),
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        if not self.text.strip() and not self.photo.strip():
            raise ValidationError({"text": "Broadcast template needs text or photo."})
