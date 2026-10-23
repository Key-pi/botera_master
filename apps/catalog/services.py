from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError

from apps.accounts.services import ensure_partner_access
from apps.catalog.models import DEFAULT_CURRENCY, Service, ServiceCategory

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.partners.models import Partner


def create_service_category(
    *,
    actor: User,
    partner: Partner,
    name: str,
    description: str = "",
    sort_order: int = 0,
    is_active: bool = True,
) -> ServiceCategory:
    """Create a partner-owned category for grouping services."""

    ensure_partner_access(actor, partner)
    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValidationError({"name": "Category name is required."})
    if sort_order < 0:
        raise ValidationError({"sort_order": "Sort order cannot be negative."})

    return ServiceCategory.objects.create(
        partner=partner,
        name=cleaned_name,
        description=description,
        sort_order=sort_order,
        is_active=is_active,
    )


def create_service(
    *,
    actor: User,
    partner: Partner,
    category: ServiceCategory,
    name: str,
    price_amount: int,
    duration_minutes: int,
    description: str = "",
    currency: str = DEFAULT_CURRENCY,
    sort_order: int = 0,
    is_active: bool = True,
) -> Service:
    """Create a partner-owned service from partner-provided catalog data."""

    ensure_partner_access(actor, partner)
    _validate_category_partner(partner=partner, category=category)
    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValidationError({"name": "Service name is required."})
    if price_amount < 0:
        raise ValidationError({"price_amount": "Price cannot be negative."})
    if duration_minutes <= 0:
        raise ValidationError({"duration_minutes": "Duration must be positive."})
    if sort_order < 0:
        raise ValidationError({"sort_order": "Sort order cannot be negative."})

    normalized_currency = currency.strip().upper()
    if len(normalized_currency) != 3:
        raise ValidationError({"currency": "Currency must be a three-letter code."})

    return Service.objects.create(
        partner=partner,
        category=category,
        name=cleaned_name,
        description=description,
        price_amount=price_amount,
        currency=normalized_currency,
        duration_minutes=duration_minutes,
        sort_order=sort_order,
        is_active=is_active,
    )


def _validate_category_partner(*, partner: Partner, category: ServiceCategory) -> None:
    if category.partner_id != partner.id:
        raise ValidationError({"category": "Service category must belong to this partner."})
