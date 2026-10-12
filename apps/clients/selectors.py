from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import QuerySet

from apps.accounts.selectors import partner_ids_for_user
from apps.clients.models import ClientProfile, TelegramIdentity
from apps.clients.phone import normalize_phone

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.partners.models import Partner


def telegram_identity_by_user_id(telegram_user_id: int) -> TelegramIdentity | None:
    """Return a Telegram identity by Telegram's stable user id."""

    return TelegramIdentity.objects.filter(telegram_user_id=telegram_user_id).first()


def client_profiles_for_partner(partner: Partner) -> QuerySet[ClientProfile]:
    """Return all client profiles for one partner, including manual profiles."""

    return ClientProfile.objects.filter(partner=partner).select_related(
        "partner",
        "telegram_identity",
    )


def client_profiles_visible_to_user(user: User) -> QuerySet[ClientProfile]:
    """Return client profiles an admin actor can see through trusted memberships."""

    queryset = ClientProfile.objects.select_related("partner", "telegram_identity")
    if user.is_superuser:
        return queryset
    return queryset.filter(partner_id__in=partner_ids_for_user(user))


def client_profile_for_telegram_identity(
    *,
    partner: Partner,
    telegram_identity: TelegramIdentity,
) -> ClientProfile | None:
    """Return the partner profile for a shared Telegram identity if it exists."""

    return (
        ClientProfile.objects.filter(
            partner=partner,
            telegram_identity=telegram_identity,
        )
        .select_related("partner", "telegram_identity")
        .first()
    )


def manual_client_link_candidates(
    *,
    partner: Partner,
    phone: str,
    exclude_profile: ClientProfile | None = None,
) -> QuerySet[ClientProfile]:
    """Suggest manual clients with the same phone; never auto-merge them."""

    normalized_phone = normalize_phone(phone)
    if not normalized_phone:
        return ClientProfile.objects.none()

    queryset = client_profiles_for_partner(partner).filter(
        telegram_identity__isnull=True,
        phone=normalized_phone,
    )
    if exclude_profile is not None:
        queryset = queryset.exclude(id=exclude_profile.id)
    return queryset
