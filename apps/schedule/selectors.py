from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import QuerySet

from apps.schedule.models import (
    AvailabilityTemplate,
    AvailabilityWindow,
    BlockedTime,
    BookingResource,
    PartnerScheduleSettings,
)

if TYPE_CHECKING:
    from datetime import datetime

    from apps.partners.models import Partner


def default_booking_resource_for_partner(partner: Partner) -> BookingResource | None:
    """Return the default solo-master resource if it exists."""

    return BookingResource.objects.filter(partner=partner, is_default=True).first()


def active_booking_resources_for_partner(partner: Partner) -> QuerySet[BookingResource]:
    """Return resources that can be used for schedule/booking operations."""

    return BookingResource.objects.filter(partner=partner, is_active=True).order_by(
        "sort_order",
        "name",
    )


def schedule_settings_for_partner(partner: Partner) -> PartnerScheduleSettings | None:
    """Return persisted schedule settings when they already exist."""

    return PartnerScheduleSettings.objects.filter(partner=partner).first()


def availability_windows_for_resource(
    *,
    partner: Partner,
    booking_resource: BookingResource,
    start_at: datetime,
    end_at: datetime,
) -> QuerySet[AvailabilityWindow]:
    """Return availability windows overlapping a search interval."""

    return AvailabilityWindow.objects.filter(
        partner=partner,
        booking_resource=booking_resource,
        start_at__lt=end_at,
        end_at__gt=start_at,
    ).order_by("start_at", "end_at")


def blocked_times_for_resource(
    *,
    partner: Partner,
    booking_resource: BookingResource,
    start_at: datetime,
    end_at: datetime,
) -> QuerySet[BlockedTime]:
    """Return blocked intervals overlapping a search interval."""

    return BlockedTime.objects.filter(
        partner=partner,
        booking_resource=booking_resource,
        start_at__lt=end_at,
        end_at__gt=start_at,
    ).order_by("start_at", "end_at")


def availability_templates_for_partner(
    partner: Partner,
    *,
    include_inactive: bool = True,
) -> QuerySet[AvailabilityTemplate]:
    """Return partner templates for admin/calendar use."""

    queryset = AvailabilityTemplate.objects.filter(partner=partner)
    if not include_inactive:
        queryset = queryset.filter(is_active=True)
    return queryset.order_by("sort_order", "name")
