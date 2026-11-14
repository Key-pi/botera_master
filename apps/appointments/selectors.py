from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, time, timedelta
from datetime import timezone as datetime_timezone
from typing import TYPE_CHECKING
from uuid import UUID

from django.db.models import Exists, OuterRef, Q, QuerySet
from django.utils import timezone

from apps.accounts.selectors import partner_ids_for_user
from apps.appointments.models import BLOCKING_APPOINTMENT_STATUSES, Appointment
from apps.finance.models import Penalty, PenaltyApplication
from apps.schedule.services import TimeInterval
from core.timezones import get_zone

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.clients.models import ClientProfile
    from apps.partners.models import Partner
    from apps.schedule.models import BookingResource


APPOINTMENT_PERIOD_TODAY = "today"
APPOINTMENT_PERIOD_TOMORROW = "tomorrow"
APPOINTMENT_PERIOD_NEXT_7_DAYS = "next_7_days"
APPOINTMENT_PERIOD_NEXT_30_DAYS = "next_30_days"
APPOINTMENT_PERIOD_UPCOMING = "upcoming"
APPOINTMENT_PERIOD_PAST = "past"
APPOINTMENT_OPERATIONAL_PERIODS = frozenset(
    {
        APPOINTMENT_PERIOD_TODAY,
        APPOINTMENT_PERIOD_TOMORROW,
        APPOINTMENT_PERIOD_NEXT_7_DAYS,
        APPOINTMENT_PERIOD_NEXT_30_DAYS,
        APPOINTMENT_PERIOD_UPCOMING,
        APPOINTMENT_PERIOD_PAST,
    }
)

APPOINTMENT_PENALTY_ANY = "any"
APPOINTMENT_PENALTY_OUTSTANDING = "outstanding"
APPOINTMENT_PENALTY_NONE = "none"
APPOINTMENT_PENALTY_STATES = frozenset(
    {
        APPOINTMENT_PENALTY_ANY,
        APPOINTMENT_PENALTY_OUTSTANDING,
        APPOINTMENT_PENALTY_NONE,
    }
)


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


def appointments_for_booking_resource_period(
    *,
    partner: Partner,
    booking_resource: BookingResource,
    start_at: datetime,
    end_at: datetime,
    statuses: Sequence[str] | None = None,
) -> QuerySet[Appointment]:
    """Return appointments overlapping one resource calendar period."""

    queryset = (
        appointments_for_partner(partner)
        .filter(
            booking_resource=booking_resource,
            start_at__lt=end_at,
            end_at__gt=start_at,
        )
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


def appointment_for_booking_resource_by_id(
    *,
    partner: Partner,
    booking_resource: BookingResource,
    appointment_id: UUID,
) -> Appointment | None:
    """Resolve one appointment inside a trusted partner/resource calendar."""

    return (
        appointments_for_partner(partner)
        .prefetch_related("service_items")
        .filter(
            id=appointment_id,
            booking_resource=booking_resource,
        )
        .first()
    )


def appointments_visible_to_user(user: User) -> QuerySet[Appointment]:
    """Return appointments an admin actor can see through trusted memberships."""

    queryset = Appointment.objects.select_related("partner", "booking_resource", "client_profile")
    if user.is_superuser:
        return queryset
    return queryset.filter(partner_id__in=partner_ids_for_user(user))


def filter_appointments_by_operational_period(
    queryset: QuerySet[Appointment],
    *,
    period: str,
    now: datetime | None = None,
) -> QuerySet[Appointment]:
    """Filter an already scoped queryset using each partner's local calendar dates."""

    if period not in APPOINTMENT_OPERATIONAL_PERIODS:
        raise ValueError("Unknown appointment operational period.")

    current_time = now or timezone.now()
    if timezone.is_naive(current_time):
        raise ValueError("Appointment period reference time must be timezone-aware.")
    if period == APPOINTMENT_PERIOD_UPCOMING:
        return queryset.filter(start_at__gte=current_time)
    if period == APPOINTMENT_PERIOD_PAST:
        return queryset.filter(start_at__lt=current_time)

    period_query = Q(pk__in=[])
    timezone_names = tuple(
        queryset.order_by()
        .values_list("partner__timezone", flat=True)
        .distinct()
    )
    for timezone_name in timezone_names:
        zone = get_zone(timezone_name)
        local_today = current_time.astimezone(zone).date()
        if period == APPOINTMENT_PERIOD_TODAY:
            start_date = local_today
            end_date = local_today + timedelta(days=1)
        elif period == APPOINTMENT_PERIOD_TOMORROW:
            start_date = local_today + timedelta(days=1)
            end_date = local_today + timedelta(days=2)
        elif period == APPOINTMENT_PERIOD_NEXT_7_DAYS:
            start_date = local_today
            end_date = local_today + timedelta(days=7)
        else:
            start_date = local_today
            end_date = local_today + timedelta(days=30)

        start_at = datetime.combine(start_date, time.min, tzinfo=zone).astimezone(
            datetime_timezone.utc
        )
        end_at = datetime.combine(end_date, time.min, tzinfo=zone).astimezone(
            datetime_timezone.utc
        )
        period_query |= Q(
            partner__timezone=timezone_name,
            start_at__gte=start_at,
            start_at__lt=end_at,
        )
    return queryset.filter(period_query)


def filter_appointments_by_penalty_state(
    queryset: QuerySet[Appointment],
    *,
    penalty_state: str,
) -> QuerySet[Appointment]:
    """Filter appointments by source or applied penalties without duplicate rows."""

    if penalty_state not in APPOINTMENT_PENALTY_STATES:
        raise ValueError("Unknown appointment penalty state.")

    source_penalties = Penalty.objects.filter(source_appointment_id=OuterRef("pk"))
    applications = PenaltyApplication.objects.filter(appointment_id=OuterRef("pk"))
    annotated = queryset.annotate(
        _has_source_penalty=Exists(source_penalties),
        _has_penalty_application=Exists(applications),
    )
    if penalty_state == APPOINTMENT_PENALTY_ANY:
        return annotated.filter(
            Q(_has_source_penalty=True) | Q(_has_penalty_application=True)
        )
    if penalty_state == APPOINTMENT_PENALTY_NONE:
        return annotated.filter(
            _has_source_penalty=False,
            _has_penalty_application=False,
        )

    outstanding_statuses = (
        Penalty.Status.UNPAID,
        Penalty.Status.APPLIED_TO_APPOINTMENT,
    )
    return annotated.annotate(
        _has_outstanding_source_penalty=Exists(
            source_penalties.filter(status__in=outstanding_statuses)
        ),
        _has_outstanding_application=Exists(
            applications.filter(
                status=PenaltyApplication.Status.ACTIVE,
                penalty__status__in=outstanding_statuses,
            )
        ),
    ).filter(
        Q(_has_outstanding_source_penalty=True)
        | Q(_has_outstanding_application=True)
    )


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
