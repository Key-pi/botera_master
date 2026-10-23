from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from datetime import timezone as datetime_timezone
from typing import TYPE_CHECKING

from django.db.models import QuerySet
from django.utils import timezone

from apps.appointments.models import Appointment
from apps.schedule.models import (
    AvailabilityTemplate,
    AvailabilityWindow,
    BlockedTime,
    BookingResource,
    PartnerScheduleSettings,
)
from core.timezones import get_zone

if TYPE_CHECKING:
    from apps.partners.models import Partner


CALENDAR_APPOINTMENT_STATUSES = (
    Appointment.Status.REQUESTED,
    Appointment.Status.CONFIRMED,
    Appointment.Status.COMPLETED,
    Appointment.Status.NO_SHOW,
)
CALENDAR_VIEW_DAY = "day"
CALENDAR_VIEW_WEEK = "week"
CALENDAR_VIEW_MONTH = "month"
CALENDAR_VIEWS = (
    CALENDAR_VIEW_DAY,
    CALENDAR_VIEW_WEEK,
    CALENDAR_VIEW_MONTH,
)
CALENDAR_ENTRY_SORT_ORDER = {
    "availability": 0,
    "blocked": 1,
    "appointment": 2,
}


@dataclass(frozen=True, slots=True)
class CalendarEntry:
    """One display segment inside a partner-local calendar day."""

    kind: str
    record: AvailabilityWindow | BlockedTime | Appointment
    display_start_at: datetime
    display_end_at: datetime
    continues_before: bool
    continues_after: bool


@dataclass(frozen=True, slots=True)
class CalendarDay:
    """One partner-local day with already sorted operational records."""

    day: date
    is_today: bool
    entries: tuple[CalendarEntry, ...]


@dataclass(frozen=True, slots=True)
class PartnerCalendarSnapshot:
    """Bounded calendar-period read model for one partner booking resource."""

    partner: Partner
    booking_resource: BookingResource
    view: str
    focus_date: date
    period_start: date
    period_end: date
    previous_period_start: date
    next_period_start: date
    current_period_start: date
    leading_blank_days: tuple[int, ...]
    days: tuple[CalendarDay, ...]


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


def availability_windows_starting_in_period(
    *,
    partner: Partner,
    booking_resource: BookingResource,
    start_at: datetime,
    end_at: datetime,
) -> QuerySet[AvailabilityWindow]:
    """Return concrete availability owned by one local-calendar source period."""

    return AvailabilityWindow.objects.filter(
        partner=partner,
        booking_resource=booking_resource,
        start_at__gte=start_at,
        start_at__lt=end_at,
    ).order_by("start_at", "end_at", "id")


def blocked_times_starting_in_period(
    *,
    partner: Partner,
    booking_resource: BookingResource,
    start_at: datetime,
    end_at: datetime,
) -> QuerySet[BlockedTime]:
    """Return blocked intervals owned by one local-calendar source period."""

    return BlockedTime.objects.filter(
        partner=partner,
        booking_resource=booking_resource,
        start_at__gte=start_at,
        start_at__lt=end_at,
    ).order_by("start_at", "end_at", "id")


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


def partner_calendar_snapshot(
    *,
    partner: Partner,
    booking_resource: BookingResource,
    selected_date: date,
    view: str = CALENDAR_VIEW_WEEK,
    now: datetime | None = None,
) -> PartnerCalendarSnapshot:
    """Compose one resource's selected period without weakening tenant scope."""

    # Appointment selectors already depend on schedule services. Importing the
    # owner selector here avoids turning that established dependency into an
    # application-startup cycle while keeping appointment query rules together.
    from apps.appointments.selectors import appointments_for_booking_resource_period

    if booking_resource.partner_id != partner.id:
        raise ValueError("Booking resource must belong to the selected partner.")
    if view not in CALENDAR_VIEWS:
        raise ValueError("Unknown calendar view.")

    zone = get_zone(partner.timezone)
    current_time = now or timezone.now()
    local_today = current_time.astimezone(zone).date()
    (
        period_start_date,
        period_end_date,
        previous_period_start,
        next_period_start,
        current_period_start,
    ) = _calendar_period_dates(
        view=view,
        selected_date=selected_date,
        local_today=local_today,
    )
    period_start = _local_midnight_utc(day=period_start_date, zone=zone)
    period_end = _local_midnight_utc(day=period_end_date + timedelta(days=1), zone=zone)

    availability_windows = tuple(
        availability_windows_for_resource(
            partner=partner,
            booking_resource=booking_resource,
            start_at=period_start,
            end_at=period_end,
        )
    )
    blocked_times = tuple(
        blocked_times_for_resource(
            partner=partner,
            booking_resource=booking_resource,
            start_at=period_start,
            end_at=period_end,
        )
    )
    appointments = tuple(
        appointments_for_booking_resource_period(
            partner=partner,
            booking_resource=booking_resource,
            start_at=period_start,
            end_at=period_end,
            statuses=CALENDAR_APPOINTMENT_STATUSES,
        )
    )

    days = tuple(
        _calendar_day(
            day=period_start_date + timedelta(days=offset),
            today=local_today,
            zone=zone,
            availability_windows=availability_windows,
            blocked_times=blocked_times,
            appointments=appointments,
        )
        for offset in range((period_end_date - period_start_date).days + 1)
    )

    return PartnerCalendarSnapshot(
        partner=partner,
        booking_resource=booking_resource,
        view=view,
        focus_date=selected_date,
        period_start=period_start_date,
        period_end=period_end_date,
        previous_period_start=previous_period_start,
        next_period_start=next_period_start,
        current_period_start=current_period_start,
        leading_blank_days=(
            tuple(range(period_start_date.weekday()))
            if view == CALENDAR_VIEW_MONTH
            else ()
        ),
        days=days,
    )


def _calendar_period_dates(
    *,
    view: str,
    selected_date: date,
    local_today: date,
) -> tuple[date, date, date, date, date]:
    """Return inclusive period bounds and navigation anchors for a calendar view."""

    if view == CALENDAR_VIEW_DAY:
        return (
            selected_date,
            selected_date,
            selected_date - timedelta(days=1),
            selected_date + timedelta(days=1),
            local_today,
        )

    if view == CALENDAR_VIEW_WEEK:
        period_start = selected_date - timedelta(days=selected_date.weekday())
        return (
            period_start,
            period_start + timedelta(days=6),
            period_start - timedelta(days=7),
            period_start + timedelta(days=7),
            local_today - timedelta(days=local_today.weekday()),
        )

    period_start = selected_date.replace(day=1)
    next_period_start = _next_month_start(period_start)
    current_period_start = local_today.replace(day=1)
    return (
        period_start,
        next_period_start - timedelta(days=1),
        (period_start - timedelta(days=1)).replace(day=1),
        next_period_start,
        current_period_start,
    )


def _next_month_start(month_start: date) -> date:
    if month_start.month == 12:
        return date(month_start.year + 1, 1, 1)
    return date(month_start.year, month_start.month + 1, 1)


def _calendar_day(
    *,
    day: date,
    today: date,
    zone,
    availability_windows: tuple[AvailabilityWindow, ...],
    blocked_times: tuple[BlockedTime, ...],
    appointments: tuple[Appointment, ...],
) -> CalendarDay:
    day_start = _local_midnight_utc(day=day, zone=zone)
    day_end = _local_midnight_utc(day=day + timedelta(days=1), zone=zone)
    entries = [
        _calendar_entry(kind="availability", record=record, day_start=day_start, day_end=day_end)
        for record in availability_windows
        if _overlaps(record=record, start_at=day_start, end_at=day_end)
    ]
    entries.extend(
        _calendar_entry(kind="blocked", record=record, day_start=day_start, day_end=day_end)
        for record in blocked_times
        if _overlaps(record=record, start_at=day_start, end_at=day_end)
    )
    entries.extend(
        _calendar_entry(
            kind="appointment",
            record=record,
            day_start=day_start,
            day_end=day_end,
        )
        for record in appointments
        if _overlaps(record=record, start_at=day_start, end_at=day_end)
    )
    entries.sort(
        key=lambda entry: (
            entry.display_start_at,
            CALENDAR_ENTRY_SORT_ORDER[entry.kind],
            entry.display_end_at,
        )
    )
    return CalendarDay(day=day, is_today=day == today, entries=tuple(entries))


def _calendar_entry(*, kind: str, record, day_start: datetime, day_end: datetime):
    return CalendarEntry(
        kind=kind,
        record=record,
        display_start_at=max(record.start_at, day_start),
        display_end_at=min(record.end_at, day_end),
        continues_before=record.start_at < day_start,
        continues_after=record.end_at > day_end,
    )


def _overlaps(*, record, start_at: datetime, end_at: datetime) -> bool:
    return record.start_at < end_at and record.end_at > start_at


def _local_midnight_utc(*, day: date, zone) -> datetime:
    return datetime.combine(day, time.min, tzinfo=zone).astimezone(datetime_timezone.utc)
