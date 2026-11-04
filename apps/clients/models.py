from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

from core.database.models import PartnerBoundModel, TimeStampedModel


class TelegramIdentity(TimeStampedModel):
    """Technical Telegram user identity shared across partner-specific profiles."""

    telegram_user_id = models.BigIntegerField(_("Telegram user ID"), unique=True, db_index=True)
    username = models.CharField(_("username"), max_length=255, blank=True)
    first_name = models.CharField(_("first name"), max_length=255, blank=True)
    last_name = models.CharField(_("last name"), max_length=255, blank=True)
    is_premium = models.BooleanField(_("Telegram Premium"), default=False)
    language_code = models.CharField(_("language code"), max_length=16, blank=True)
    last_seen_at = models.DateTimeField(_("last seen at"), null=True, blank=True)

    class Meta:
        verbose_name = _("Telegram identity")
        verbose_name_plural = _("Telegram identities")
        ordering = ["telegram_user_id"]

    def __str__(self) -> str:
        username = f"@{self.username}" if self.username else str(self.telegram_user_id)
        return username


class ClientProfile(PartnerBoundModel):
    """Partner-scoped client data for a Telegram or manually-created client."""

    telegram_identity = models.ForeignKey(
        "clients.TelegramIdentity",
        on_delete=models.SET_NULL,
        related_name="client_profiles",
        null=True,
        blank=True,
        verbose_name=_("Telegram identity"),
    )
    name = models.CharField(_("client name"), max_length=255, blank=True)
    phone = models.CharField(_("phone"), max_length=32, blank=True)
    language = models.CharField(_("language"), max_length=8, default="uk")
    private_master_note = models.TextField(_("private master note"), blank=True)
    extra_client_text = models.TextField(_("extra client text"), blank=True)
    marketing_opt_out = models.BooleanField(_("marketing opt-out"), default=False)
    bot_unreachable = models.BooleanField(_("bot unreachable"), default=False)
    last_delivery_failure_code = models.CharField(
        _("last delivery failure code"), max_length=128, blank=True
    )
    last_delivery_failure_at = models.DateTimeField(
        _("last delivery failure at"), null=True, blank=True
    )

    class Meta:
        verbose_name = _("client profile")
        verbose_name_plural = _("client profiles")
        ordering = ["partner__name", "name", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["partner", "telegram_identity"],
                condition=models.Q(telegram_identity__isnull=False),
                name="clients_unique_partner_telegram_identity",
            ),
        ]
        indexes = [
            models.Index(fields=["partner", "name"]),
            models.Index(fields=["partner", "phone"]),
            models.Index(fields=["telegram_identity"]),
            models.Index(fields=["partner", "marketing_opt_out"]),
            models.Index(fields=["partner", "bot_unreachable"]),
        ]

    def __str__(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        if self.name:
            return self.name
        if self.telegram_identity_id:
            return str(self.telegram_identity)
        if self.phone:
            return self.phone
        return str(_("Manual client"))

    @property
    def has_telegram_identity(self) -> bool:
        return self.telegram_identity_id is not None
