from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.accounts.services import ensure_partner_access
from apps.catalog.models import DEFAULT_CURRENCY, Service, ServiceCategory

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.partners.models import Partner


@dataclass(frozen=True, slots=True)
class ServiceCategoryDetails:
    """Normalized partner-controlled category values."""

    name: str
    description: str
    sort_order: int
    is_active: bool


@dataclass(frozen=True, slots=True)
class ServiceDetails:
    """Normalized partner-controlled service values."""

    name: str
    description: str
    price_amount: int
    currency: str
    duration_minutes: int
    sort_order: int
    is_active: bool


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
    details = validate_service_category_input(
        name=name,
        description=description,
        sort_order=sort_order,
        is_active=is_active,
    )

    return ServiceCategory.objects.create(
        partner=partner,
        name=details.name,
        description=details.description,
        sort_order=details.sort_order,
        is_active=details.is_active,
    )


def update_service_category(
    *,
    actor: User,
    category: ServiceCategory,
    name: str,
    description: str = "",
    sort_order: int = 0,
    is_active: bool = True,
) -> ServiceCategory:
    """Update category metadata inside its trusted partner scope."""

    with transaction.atomic():
        stored = (
            ServiceCategory.objects.select_for_update()
            .select_related("partner")
            .get(id=category.id)
        )
        ensure_partner_access(actor, stored.partner)
        details = validate_service_category_input(
            name=name,
            description=description,
            sort_order=sort_order,
            is_active=is_active,
        )
        stored.name = details.name
        stored.description = details.description
        stored.sort_order = details.sort_order
        stored.is_active = details.is_active
        stored.save(
            update_fields=[
                "name",
                "description",
                "sort_order",
                "is_active",
                "updated_at",
            ]
        )
        return stored


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
    details = validate_service_input(
        partner=partner,
        category=category,
        name=name,
        description=description,
        price_amount=price_amount,
        currency=currency,
        duration_minutes=duration_minutes,
        sort_order=sort_order,
        is_active=is_active,
    )

    return Service.objects.create(
        partner=partner,
        category=category,
        name=details.name,
        description=details.description,
        price_amount=details.price_amount,
        currency=details.currency,
        duration_minutes=details.duration_minutes,
        sort_order=details.sort_order,
        is_active=details.is_active,
    )


def update_service(
    *,
    actor: User,
    service: Service,
    category: ServiceCategory,
    name: str,
    price_amount: int,
    duration_minutes: int,
    description: str = "",
    currency: str = DEFAULT_CURRENCY,
    sort_order: int = 0,
    is_active: bool = True,
) -> Service:
    """Update a catalog service without changing historical appointment snapshots."""

    with transaction.atomic():
        stored = Service.objects.select_for_update().select_related("partner").get(id=service.id)
        ensure_partner_access(actor, stored.partner)
        details = validate_service_input(
            partner=stored.partner,
            category=category,
            name=name,
            description=description,
            price_amount=price_amount,
            currency=currency,
            duration_minutes=duration_minutes,
            sort_order=sort_order,
            is_active=is_active,
        )
        stored.category = category
        stored.name = details.name
        stored.description = details.description
        stored.price_amount = details.price_amount
        stored.currency = details.currency
        stored.duration_minutes = details.duration_minutes
        stored.sort_order = details.sort_order
        stored.is_active = details.is_active
        stored.save(
            update_fields=[
                "category",
                "name",
                "description",
                "price_amount",
                "currency",
                "duration_minutes",
                "sort_order",
                "is_active",
                "updated_at",
            ]
        )
        return stored


def validate_service_category_input(
    *,
    name: str,
    description: str,
    sort_order: int,
    is_active: bool,
) -> ServiceCategoryDetails:
    """Normalize reusable category values for forms and write services."""

    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValidationError({"name": "Category name is required."})
    if sort_order < 0:
        raise ValidationError({"sort_order": "Sort order cannot be negative."})
    return ServiceCategoryDetails(
        name=cleaned_name,
        description=description.strip(),
        sort_order=sort_order,
        is_active=bool(is_active),
    )


def validate_service_input(
    *,
    partner: Partner,
    category: ServiceCategory,
    name: str,
    description: str,
    price_amount: int,
    currency: str,
    duration_minutes: int,
    sort_order: int,
    is_active: bool,
) -> ServiceDetails:
    """Normalize reusable service values for forms and write services."""

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
    return ServiceDetails(
        name=cleaned_name,
        description=description.strip(),
        price_amount=price_amount,
        currency=normalized_currency,
        duration_minutes=duration_minutes,
        sort_order=sort_order,
        is_active=bool(is_active),
    )


def _validate_category_partner(*, partner: Partner, category: ServiceCategory) -> None:
    if category.partner_id != partner.id:
        raise ValidationError({"category": "Service category must belong to this partner."})
