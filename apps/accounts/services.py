from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.exceptions import PermissionDenied

from apps.accounts.selectors import user_can_access_partner

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.partners.models import Partner


def ensure_partner_access(user: User, partner: Partner) -> None:
    """Raise when a user attempts to operate outside their trusted partner scope."""

    if not user_can_access_partner(user, partner):
        raise PermissionDenied("User cannot access this partner.")
