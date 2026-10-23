from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING
from uuid import UUID

from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.accounts.selectors import partner_ids_for_user
from apps.appointments.models import BLOCKING_APPOINTMENT_STATUSES, Appointment
from apps.schedule.services import TimeInterval

if TYPE_CHECKING:
    from datetime import datetime

    from apps.accounts.models import User
    from apps.clients.models import ClientProfile
    from apps.partners.models import Partner
    from apps.schedule.models import BookingResource


def appointments_for_partner(partner: Partner) -> QuerySet[Appointment]:
    """Return partner appointments with common detail relations preloaded."""

    return Appointment.objects.filter(partner=partner).select_related(
        "partner",
        "booking_resource",
        "client_profile",
        "created_by",
    )


def appointments_for_partner_period(
    *,
    partner: Partner,
    start_at: datetime,
    end_at: datetime,
    statuses: Sequence[str] | None = None,
) -> QuerySet[Appointment]:
    """Return appointment summaries for one tenant and half-open time period."""

    queryset = (
        appointments_for_partner(partner)
        .filter(start_at__gte=start_at, start_at__lt=end_at)
        .prefetch_related("service_items")
    )
    if statuses is not None:
        queryset = queryset.filter(status__in=statuses)
    return queryset.order_by("start_at", "created_at")


def requested_appointments_for_partner(*, partner: Partner) -> QuerySet[Appointment]:
    """Return outstanding booking requests that need this partner's attention."""

    return (
        appointments_for_partner(partner)
        .filter(status=Appointment.Status.REQUESTED)
        .prefetch_related("service_items")
        .order_by("start_at", "created_at")
    )


def appointment_for_partner_by_id(
    *,
    partner: Partner,
    appointment_id: UUID,
) -> Appointment | None:
    """Return one partner-scoped appointment with bot/admin detail relations."""

    return (
        appointments_for_partner(partner)
        .prefetch_related("service_items")
        .filter(id=appointment_id)
        .first()
    )


def appointments_visible_to_user(user: User) -> QuerySet[Appointment]:
    """Return appointments an admin actor can see through trusted memberships."""

    queryset = Appointment.objects.select_related("partner", "booking_resource", "client_profile")
    if user.is_superuser:
        return queryset
    return queryset.filter(partner_id__in=partner_ids_for_user(user))


def appointments_for_client(client_profile: ClientProfile) -> QuerySet[Appointment]:
    """Return all appointment history for one partner-scoped client profile."""

    return Appointment.objects.filter(client_profile=client_profile).select_related(
        "partner",
        "booking_resource",
    )


def future_appointments_for_client(
    *,
    client_profile: ClientProfile,
    now: datetime | None = None,
) -> QuerySet[Appointment]:
    """Return future active requests/appointments for a partner client card."""

    current_time = now or timezone.now()
    return (
        appointments_for_client(client_profile)
        .filter(
            partner=client_profile.partner,
            status__in=[Appointment.Status.REQUESTED, Appointment.Status.CONFIRMED],
            start_at__gte=current_time,
        )
        .prefetch_related("service_items")
        .order_by("start_at", "created_at")
    )


def appointment_history_for_client(
    *,
    client_profile: ClientProfile,
    now: datetime | None = None,
) -> QuerySet[Appointment]:
    """Return non-future-active records so the card lists each appointment once."""

    current_time = now or timezone.now()
    future_active = Q(
        status__in=[Appointment.Status.REQUESTED, Appointment.Status.CONFIRMED],
        start_at__gte=current_time,
    )
    return (
        appointments_for_client(client_profile)
        .filter(partner=client_profile.partner)
        .exclude(future_active)
        .prefetch_related("service_items")
        .order_by("-start_at", "-created_at")
    )


def confirmed_appointments_overlapping(
    *,
    booking_resource: BookingResource,
    start_at: datetime,
    end_at: datetime,
    exclude_appointment: Appointment | None = None,
) -> QuerySet[Appointment]:
    """Return confirmed appointments that overlap a candidate interval."""

    queryset = Appointment.objects.filter(
        booking_resource=booking_resource,
        status__in=BLOCKING_APPOINTMENT_STATUSES,
        start_at__lt=end_at,
        end_at__gt=start_at,
    )
    if exclude_appointment is not None:
        queryset = queryset.exclude(id=exclude_appointment.id)
    return queryset.order_by("start_at")


def confirmed_busy_intervals_for_resource(
    *,
    booking_resource: BookingResource,
    start_at: datetime,
    end_at: datetime,
    exclude_appointment: Appointment | None = None,
) -> tuple[TimeInterval, ...]:
    """Return confirmed appointment intervals for schedule slot generation."""

    appointments = confirmed_appointments_overlapping(
        booking_resource=booking_resource,
        start_at=start_at,
        end_at=end_at,
        exclude_appointment=exclude_appointment,
    ).only("start_at", "end_at")
    return tuple(TimeInterval(item.start_at, item.end_at) for item in appointments)
