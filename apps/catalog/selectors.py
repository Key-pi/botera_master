from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import QuerySet

from apps.catalog.models import Service, ServiceCategory

if TYPE_CHECKING:
    from apps.partners.models import Partner


def categories_for_partner(
    partner: Partner,
    *,
    include_inactive: bool = True,
) -> QuerySet[ServiceCategory]:
    """Return categories for partner admin views."""

    queryset = ServiceCategory.objects.filter(partner=partner)
    if not include_inactive:
        queryset = queryset.filter(is_active=True)
    return queryset


def active_categories_for_booking(partner: Partner) -> QuerySet[ServiceCategory]:
    """Return active categories that currently expose at least one active service."""

    return (
        ServiceCategory.objects.filter(
            partner=partner,
            is_active=True,
            services__is_active=True,
        )
        .distinct()
        .order_by("sort_order", "name")
    )


def services_for_partner(
    partner: Partner,
    *,
    include_inactive: bool = True,
) -> QuerySet[Service]:
    """Return partner services for admin/history contexts."""

    queryset = Service.objects.filter(partner=partner).select_related("category", "partner")
    if not include_inactive:
        queryset = queryset.filter(is_active=True)
    return queryset


def active_services_for_booking(partner: Partner) -> QuerySet[Service]:
    """Return services available for new client bookings."""

    return services_for_partner(partner, include_inactive=False).filter(category__is_active=True)
