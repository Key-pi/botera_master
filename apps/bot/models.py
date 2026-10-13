from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.database.models import PartnerBoundModel, TimeStampedModel

SUPPORTED_TEMPLATE_LANGUAGES = ("uk", "ru", "en")
DEFAULT_TEMPLATE_LANGUAGE = "uk"
TEMPLATE_LANGUAGE_CHOICES = tuple(
    (language, language.upper()) for language in SUPPORTED_TEMPLATE_LANGUAGES
)


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


class PartnerTelegramContact(PartnerBoundModel):
    """Partner-scoped Telegram recipient for master-facing delivery."""

    class Role(models.TextChoices):
        OWNER_MASTER = "owner_master", "Owner/master"
        ADMIN_RECIPIENT = "admin_recipient", "Admin recipient"

    telegram_identity = models.ForeignKey(
        "clients.TelegramIdentity",
        on_delete=models.CASCADE,
        related_name="partner_master_contacts",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="partner_telegram_contacts",
        null=True,
        blank=True,
    )
    role = models.CharField(max_length=32, choices=Role.choices, default=Role.OWNER_MASTER)
    is_primary = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    linked_at = models.DateTimeField(default=timezone.now)
    last_delivery_failure_code = models.CharField(max_length=128, blank=True)
    last_delivery_failure_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["partner__name", "-is_primary", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "telegram_identity"],
                name="bot_unique_partner_master_telegram_identity",
            ),
            models.UniqueConstraint(
                fields=["partner"],
                condition=models.Q(is_primary=True, is_active=True),
                name="bot_unique_active_primary_master_contact",
            ),
        ]
        indexes = [
            models.Index(fields=["partner", "is_active", "is_primary"]),
            models.Index(fields=["telegram_identity", "is_active"]),
            models.Index(fields=["user", "is_active"]),
        ]

    def __str__(self) -> str:
        marker = "primary" if self.is_primary else "secondary"
        return f"{self.partner} master contact {self.telegram_identity} ({marker})"


class PartnerTelegramContactLink(PartnerBoundModel):
    """Short-lived signed deep-link nonce for binding a master Telegram contact."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="partner_telegram_contact_links",
    )
    nonce_fingerprint = models.CharField(max_length=64, unique=True, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["partner", "user", "expires_at"]),
            models.Index(fields=["partner", "consumed_at", "expires_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.partner} Telegram contact link for {self.user}"

    @property
    def is_consumed(self) -> bool:
        return self.consumed_at is not None


class BotTemplateContentType(models.TextChoices):
    MESSAGE = "message", "Message"
    BUTTON = "button", "Button"


class SystemTemplate(TimeStampedModel):
    """Platform-owned localized bot content default for one key/language."""

    key = models.CharField(max_length=128, db_index=True)
    language = models.CharField(max_length=8, choices=TEMPLATE_LANGUAGE_CHOICES)
    body = models.TextField()
    content_type = models.CharField(
        max_length=16,
        choices=BotTemplateContentType.choices,
        default=BotTemplateContentType.MESSAGE,
    )
    editable = models.BooleanField(default=True)
    is_required = models.BooleanField(default=True)
    placeholder_allowlist = models.JSONField(default=list, blank=True)
    sample_context = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["key", "language"]
        constraints = [
            models.UniqueConstraint(
                fields=["key", "language"],
                name="bot_unique_system_template_language",
            ),
        ]
        indexes = [
            models.Index(fields=["key", "language"]),
            models.Index(fields=["editable", "content_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.key} [{self.language}]"

    def clean(self) -> None:
        super().clean()
        _validate_language(self.language)
        from apps.bot.services import validate_template_body

        validate_template_body(
            body=self.body,
            allowed_placeholders=tuple(self.placeholder_allowlist or ()),
            sample_context=self.sample_context or {},
            content_type=self.content_type,
        )


class PartnerTemplateOverride(PartnerBoundModel):
    """Partner-authored localized override for an editable system template."""

    key = models.CharField(max_length=128, db_index=True)
    language = models.CharField(max_length=8, choices=TEMPLATE_LANGUAGE_CHOICES)
    body = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    last_validated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["partner__name", "key", "language"]
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "key", "language"],
                name="bot_unique_partner_template_override_language",
            ),
        ]
        indexes = [
            models.Index(fields=["partner", "key", "language", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.partner} {self.key} [{self.language}]"

    def clean(self) -> None:
        super().clean()
        _validate_language(self.language)
        from apps.bot.services import _system_template_for_key_language, validate_template_body

        system_template = _system_template_for_key_language(
            key=self.key,
            language=self.language,
        )
        if not system_template.editable:
            raise ValidationError({"key": "This template is system-only."})
        if self.body.strip():
            validate_template_body(
                body=self.body,
                allowed_placeholders=tuple(system_template.placeholder_allowlist),
                sample_context=system_template.sample_context,
                content_type=system_template.content_type,
            )


def _validate_language(language: str) -> None:
    if language not in SUPPORTED_TEMPLATE_LANGUAGES:
        raise ValidationError({"language": "Unsupported template language."})
