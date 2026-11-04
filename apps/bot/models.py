from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.database.models import PartnerBoundModel, TimeStampedModel

SUPPORTED_TEMPLATE_LANGUAGES = ("uk", "ru", "en")
DEFAULT_TEMPLATE_LANGUAGE = "uk"
TEMPLATE_LANGUAGE_CHOICES = tuple(
    (language, language.upper()) for language in SUPPORTED_TEMPLATE_LANGUAGES
)


class BotInstance(TimeStampedModel):
    """Technical Telegram bot connection owned by one partner."""

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        PAUSED = "paused", _("Paused")
        SUSPENDED = "suspended", _("Suspended")
        ERROR = "error", _("Error")

    class DeliveryMode(models.TextChoices):
        POLLING = "polling", _("Polling")

    partner = models.OneToOneField(
        "partners.Partner",
        on_delete=models.CASCADE,
        related_name="bot_instance",
        verbose_name=_("partner"),
    )
    status = models.CharField(
        _("status"), max_length=16, choices=Status.choices, default=Status.PAUSED
    )
    delivery_mode = models.CharField(
        _("delivery mode"),
        max_length=16,
        choices=DeliveryMode.choices,
        default=DeliveryMode.POLLING,
    )
    encrypted_token = models.TextField(_("encrypted token"), blank=True)
    token_key_id = models.CharField(_("token key ID"), max_length=64, blank=True)
    token_fingerprint = models.CharField(
        _("token fingerprint"),
        max_length=64,
        null=True,
        blank=True,
        unique=True,
        db_index=True,
    )
    token_masked = models.CharField(_("masked token"), max_length=64, blank=True)
    telegram_bot_id = models.BigIntegerField(_("Telegram bot ID"), null=True, blank=True)
    telegram_username = models.CharField(_("Telegram username"), max_length=255, blank=True)
    last_error_code = models.CharField(_("last error code"), max_length=128, blank=True)
    last_error_message_masked = models.TextField(_("masked last error message"), blank=True)
    last_checked_at = models.DateTimeField(_("last checked at"), null=True, blank=True)
    last_started_at = models.DateTimeField(_("last started at"), null=True, blank=True)
    last_activity_at = models.DateTimeField(_("last activity at"), null=True, blank=True)

    class Meta:
        verbose_name = _("bot instance")
        verbose_name_plural = _("bot instances")
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
        OWNER_MASTER = "owner_master", _("Owner/master")
        ADMIN_RECIPIENT = "admin_recipient", _("Admin recipient")

    telegram_identity = models.ForeignKey(
        "clients.TelegramIdentity",
        on_delete=models.CASCADE,
        related_name="partner_master_contacts",
        verbose_name=_("Telegram identity"),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="partner_telegram_contacts",
        null=True,
        blank=True,
        verbose_name=_("user"),
    )
    role = models.CharField(
        _("role"), max_length=32, choices=Role.choices, default=Role.OWNER_MASTER
    )
    is_primary = models.BooleanField(_("primary"), default=False)
    is_active = models.BooleanField(_("active"), default=True)
    linked_at = models.DateTimeField(_("linked at"), default=timezone.now)
    last_delivery_failure_code = models.CharField(
        _("last delivery failure code"), max_length=128, blank=True
    )
    last_delivery_failure_at = models.DateTimeField(
        _("last delivery failure at"), null=True, blank=True
    )

    class Meta:
        verbose_name = _("partner Telegram contact")
        verbose_name_plural = _("partner Telegram contacts")
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
        verbose_name=_("user"),
    )
    nonce_fingerprint = models.CharField(
        _("nonce fingerprint"), max_length=64, unique=True, db_index=True
    )
    expires_at = models.DateTimeField(_("expires at"), db_index=True)
    consumed_at = models.DateTimeField(_("consumed at"), null=True, blank=True)

    class Meta:
        verbose_name = _("partner Telegram contact link")
        verbose_name_plural = _("partner Telegram contact links")
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
    MESSAGE = "message", _("Message")
    BUTTON = "button", _("Button")


class SystemTemplate(TimeStampedModel):
    """Platform-owned localized bot content default for one key/language."""

    key = models.CharField(_("key"), max_length=128, db_index=True)
    language = models.CharField(_("language"), max_length=8, choices=TEMPLATE_LANGUAGE_CHOICES)
    body = models.TextField(_("body"))
    content_type = models.CharField(
        _("content type"),
        max_length=16,
        choices=BotTemplateContentType.choices,
        default=BotTemplateContentType.MESSAGE,
    )
    editable = models.BooleanField(_("editable"), default=True)
    is_required = models.BooleanField(_("required"), default=True)
    placeholder_allowlist = models.JSONField(_("allowed placeholders"), default=list, blank=True)
    sample_context = models.JSONField(_("sample context"), default=dict, blank=True)

    class Meta:
        verbose_name = _("system template")
        verbose_name_plural = _("system templates")
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

    key = models.CharField(_("key"), max_length=128, db_index=True)
    language = models.CharField(_("language"), max_length=8, choices=TEMPLATE_LANGUAGE_CHOICES)
    body = models.TextField(_("body"), blank=True)
    is_active = models.BooleanField(_("active"), default=True)
    last_validated_at = models.DateTimeField(_("last validated at"), null=True, blank=True)

    class Meta:
        verbose_name = _("partner template override")
        verbose_name_plural = _("partner template overrides")
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
