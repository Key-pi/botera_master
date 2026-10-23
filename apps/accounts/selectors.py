from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import QuerySet

from apps.accounts.models import PartnerMembership

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.partners.models import Partner


def active_owner_memberships_for_user(user: User) -> QuerySet[PartnerMembership]:
    """Return active owner memberships for an authenticated admin user."""

    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return PartnerMembership.objects.none()

    return PartnerMembership.objects.filter(
        user=user,
        role=PartnerMembership.Role.OWNER,
        is_active=True,
    ).select_related("partner", "user")


def partner_ids_for_user(user: User) -> QuerySet:
    """Return partner ids the user may operate inside."""

    return active_owner_memberships_for_user(user).values_list("partner_id", flat=True)


def user_can_access_partner(user: User, partner: Partner) -> bool:
    """Check tenant access using trusted membership records, not request payloads."""

    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    if user.is_superuser:
        return True

    return active_owner_memberships_for_user(user).filter(partner=partner).exists()
