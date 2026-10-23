from __future__ import annotations

from django.db.models import QuerySet

from apps.bot.models import BotInstance, PartnerTelegramContact
from apps.partners.models import Partner


def active_polling_bot_instances() -> QuerySet[BotInstance]:
    """Return bot records that are operationally active before setup/token checks."""

    return BotInstance.objects.select_related("partner").filter(
        status=BotInstance.Status.ACTIVE,
        delivery_mode=BotInstance.DeliveryMode.POLLING,
    )


def bot_instance_for_partner(partner: Partner) -> BotInstance | None:
    """Return the technical bot state for one partner without creating it."""

    return BotInstance.objects.filter(partner=partner).first()


def active_master_telegram_contacts(partner: Partner) -> QuerySet[PartnerTelegramContact]:
    """Return active partner contacts for master-facing Telegram delivery."""

    return (
        PartnerTelegramContact.objects.filter(partner=partner, is_active=True)
        .select_related("partner", "telegram_identity", "user")
        .order_by("-is_primary", "created_at")
    )


def primary_master_telegram_contact(partner: Partner) -> PartnerTelegramContact | None:
    """Return the primary active master Telegram contact, if configured."""

    return active_master_telegram_contacts(partner).filter(is_primary=True).first()


def master_telegram_contact_for_user(
    *,
    partner: Partner,
    telegram_user_id: int,
) -> PartnerTelegramContact | None:
    """Resolve an active master contact from trusted bot and Telegram contexts."""

    return (
        active_master_telegram_contacts(partner)
        .filter(
            telegram_identity__telegram_user_id=telegram_user_id,
            user__isnull=False,
        )
        .first()
    )
