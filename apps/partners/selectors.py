from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import QuerySet

from apps.accounts.models import PartnerMembership
from apps.partners.models import Partner
from apps.partners.setup import get_partner_setup_status

if TYPE_CHECKING:
    from apps.accounts.models import User


def visible_partners_for_user(user: User) -> QuerySet[Partner]:
    """Return partners visible to a platform or partner-admin user."""

    queryset = Partner.objects.all()
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return queryset.none()
    if user.is_superuser:
        return queryset

    return queryset.filter(
        memberships__user=user,
        memberships__role=PartnerMembership.Role.OWNER,
        memberships__is_active=True,
    ).distinct()


def setup_required_partners() -> QuerySet[Partner]:
    """Partners that are still in onboarding/setup state."""

    return Partner.objects.filter(status=Partner.Status.SETUP_REQUIRED)


def partners_with_missing_setup() -> list[tuple[Partner, tuple[str, ...]]]:
    """Small superuser helper for setup/problem views."""

    result: list[tuple[Partner, tuple[str, ...]]] = []
    for partner in setup_required_partners():
        status = get_partner_setup_status(partner)
        result.append((partner, status.missing_steps))
    return result
