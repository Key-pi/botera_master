from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from datetime import timezone as datetime_timezone
from typing import TYPE_CHECKING
from uuid import UUID

from django.db.models import QuerySet
from django.utils import timezone

from apps.appointments.models import Appointment
from apps.schedule.models import (
    DEFAULT_NEAR_FIT_TOLERANCE_MINUTES,
    MAX_BOOKING_HORIZON_DAYS,
    AvailabilityTemplate,
    AvailabilityWindow,
    BlockedTime,
    BookingResource,
    PartnerScheduleSettings,
    ScheduleConflictAcknowledgement,
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
SCHEDULE_CONFLICT_INACTIVE_RESOURCE = "inactive_resource"
SCHEDULE_CONFLICT_BLOCKED_TIME = "blocked_time"
SCHEDULE_CONFLICT_OUTSIDE_AVAILABILITY = "outside_availability"
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
class ScheduleConflict:
    """Computed schedule problem for one unchanged confirmed appointment."""

    appointment: Appointment
    reasons: tuple[str, ...]
    overlapping_blocked_times: tuple[BlockedTime, ...]
    fingerprint: str
    acknowledgement: ScheduleConflictAcknowledgement | None = None

    @property
    def is_acknowledged(self) -> bool:
        return self.acknowledgement is not None


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
    conflicts: tuple[ScheduleConflict, ...]

    @property
    def conflicted_appointment_ids(self) -> frozenset[object]:
        return frozenset(conflict.appointment.id for conflict in self.conflicts)


def default_booking_resource_for_partner(partner: Partner) -> BookingResource | None:
    """Return the default solo-master resource if it exists."""

    return BookingResource.objects.filter(partner=partner, is_default=True).first()


def active_booking_resources_for_partner(partner: Partner) -> QuerySet[BookingResource]:
    """Return resources that can be used for schedule/booking operations."""

    return BookingResource.objects.filter(partner=partner, is_active=True).order_by(
        "sort_order",
        "name",
    )


def booking_resources_for_partner(partner: Partner) -> QuerySet[BookingResource]:
    """Return all resources so inactive-resource conflicts remain visible."""

    return BookingResource.objects.filter(partner=partner).order_by(
        "sort_order",
        "name",
    )


def schedule_settings_for_partner(partner: Partner) -> PartnerScheduleSettings | None:
    """Return persisted schedule settings when they already exist."""

    return PartnerScheduleSettings.objects.filter(partner=partner).first()


def upcoming_schedule_conflicts_for_partner(
    *,
    partner: Partner,
    booking_resource: BookingResource | None = None,
    now: datetime | None = None,
    include_acknowledged: bool = False,
) -> tuple[ScheduleConflict, ...]:
    """Return bounded future conflicts for the partner operations screen."""

    current_time = now or timezone.now()
    return schedule_conflicts_for_partner_period(
        partner=partner,
        booking_resource=booking_resource,
        start_at=current_time,
        end_at=current_time + timedelta(days=MAX_BOOKING_HORIZON_DAYS),
        include_acknowledged=include_acknowledged,
    )


def schedule_conflicts_for_partner_period(
    *,
    partner: Partner,
    start_at: datetime,
    end_at: datetime,
    booking_resource: BookingResource | None = None,
    include_acknowledged: bool = False,
) -> tuple[ScheduleConflict, ...]:
    """Compute confirmed appointments made invalid by the current schedule."""

    if timezone.is_naive(start_at) or timezone.is_naive(end_at):
        raise ValueError("Schedule conflict bounds must be timezone-aware.")
    if start_at >= end_at:
        raise ValueError("Schedule conflict end must be after start.")
    if booking_resource is not None and booking_resource.partner_id != partner.id:
        raise ValueError("Booking resource must belong to the selected partner.")

    appointments = Appointment.objects.filter(
        partner=partner,
        status=Appointment.Status.CONFIRMED,
        start_at__lt=end_at,
        end_at__gt=start_at,
    )
    windows = AvailabilityWindow.objects.filter(
        partner=partner,
        start_at__lt=end_at,
        end_at__gt=start_at,
    )
    blocked_times = BlockedTime.objects.filter(
        partner=partner,
        start_at__lt=end_at,
        end_at__gt=start_at,
    )
    if booking_resource is not None:
        appointments = appointments.filter(booking_resource=booking_resource)
        windows = windows.filter(booking_resource=booking_resource)
        blocked_times = blocked_times.filter(booking_resource=booking_resource)

    appointment_records = tuple(
        appointments.select_related(
            "partner",
            "booking_resource",
            "client_profile",
            "client_profile__telegram_identity",
        )
        .prefetch_related("service_items")
        .order_by("start_at", "created_at")
    )
    settings = schedule_settings_for_partner(partner)
    tolerance_minutes = (
        settings.near_fit_tolerance_minutes
        if settings is not None
        else DEFAULT_NEAR_FIT_TOLERANCE_MINUTES
    )
    return _schedule_conflicts_from_records(
        appointments=appointment_records,
        availability_windows=tuple(windows.order_by("start_at", "end_at")),
        blocked_times=tuple(blocked_times.order_by("start_at", "end_at")),
        near_fit_tolerance_minutes=tolerance_minutes,
        acknowledgements_by_appointment=_acknowledgements_by_appointment(
            partner=partner,
            appointment_ids=(appointment.id for appointment in appointment_records),
        ),
        include_acknowledged=include_acknowledged,
    )


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
    settings = schedule_settings_for_partner(partner)
    conflicts = _schedule_conflicts_from_records(
        appointments=tuple(
            appointment
            for appointment in appointments
            if appointment.status == Appointment.Status.CONFIRMED
            and appointment.end_at > current_time
        ),
        availability_windows=availability_windows,
        blocked_times=blocked_times,
        near_fit_tolerance_minutes=(
            settings.near_fit_tolerance_minutes
            if settings is not None
            else DEFAULT_NEAR_FIT_TOLERANCE_MINUTES
        ),
        acknowledgements_by_appointment=_acknowledgements_by_appointment(
            partner=partner,
            appointment_ids=(appointment.id for appointment in appointments),
        ),
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
        conflicts=conflicts,
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


def _schedule_conflicts_from_records(
    *,
    appointments: tuple[Appointment, ...],
    availability_windows: tuple[AvailabilityWindow, ...],
    blocked_times: tuple[BlockedTime, ...],
    near_fit_tolerance_minutes: int,
    acknowledgements_by_appointment: dict[
        object,
        ScheduleConflictAcknowledgement,
    ] | None = None,
    include_acknowledged: bool = False,
) -> tuple[ScheduleConflict, ...]:
    windows_by_resource: dict[object, list[AvailabilityWindow]] = {}
    for window in availability_windows:
        windows_by_resource.setdefault(window.booking_resource_id, []).append(window)

    blocks_by_resource: dict[object, list[BlockedTime]] = {}
    for blocked_time in blocked_times:
        blocks_by_resource.setdefault(blocked_time.booking_resource_id, []).append(blocked_time)

    merged_windows_by_resource = {
        resource_id: _merge_availability_windows(windows)
        for resource_id, windows in windows_by_resource.items()
    }
    tolerance = timedelta(minutes=near_fit_tolerance_minutes)
    conflicts: list[ScheduleConflict] = []
    for appointment in appointments:
        reasons: list[str] = []
        if not appointment.booking_resource.is_active:
            reasons.append(SCHEDULE_CONFLICT_INACTIVE_RESOURCE)

        overlapping_blocks = tuple(
            blocked_time
            for blocked_time in blocks_by_resource.get(appointment.booking_resource_id, ())
            if appointment.start_at < blocked_time.end_at
            and appointment.end_at > blocked_time.start_at
        )
        if overlapping_blocks:
            reasons.append(SCHEDULE_CONFLICT_BLOCKED_TIME)

        merged_windows = merged_windows_by_resource.get(appointment.booking_resource_id, ())
        if not _appointment_fits_availability(
            appointment=appointment,
            merged_windows=merged_windows,
            near_fit_tolerance=tolerance,
        ):
            reasons.append(SCHEDULE_CONFLICT_OUTSIDE_AVAILABILITY)

        if reasons:
            fingerprint = _schedule_conflict_fingerprint(
                appointment=appointment,
                reasons=tuple(reasons),
                merged_windows=merged_windows,
                overlapping_blocks=overlapping_blocks,
                near_fit_tolerance_minutes=near_fit_tolerance_minutes,
            )
            acknowledgement = (acknowledgements_by_appointment or {}).get(appointment.id)
            if acknowledgement is not None and acknowledgement.fingerprint != fingerprint:
                acknowledgement = None
            if acknowledgement is not None and not include_acknowledged:
                continue
            conflicts.append(
                ScheduleConflict(
                    appointment=appointment,
                    reasons=tuple(reasons),
                    overlapping_blocked_times=overlapping_blocks,
                    fingerprint=fingerprint,
                    acknowledgement=acknowledgement,
                )
            )
    return tuple(conflicts)


def _acknowledgements_by_appointment(
    *,
    partner: Partner,
    appointment_ids: Iterable[UUID],
) -> dict[object, ScheduleConflictAcknowledgement]:
    ids = tuple(appointment_ids)
    if not ids:
        return {}
    return {
        acknowledgement.appointment_id: acknowledgement
        for acknowledgement in ScheduleConflictAcknowledgement.objects.filter(
            partner=partner,
            appointment_id__in=ids,
        )
    }


def _schedule_conflict_fingerprint(
    *,
    appointment: Appointment,
    reasons: tuple[str, ...],
    merged_windows: tuple[tuple[datetime, datetime], ...],
    overlapping_blocks: tuple[BlockedTime, ...],
    near_fit_tolerance_minutes: int,
) -> str:
    """Hash only schedule facts that can change this appointment's conflict."""

    tolerance = (
        timedelta(minutes=near_fit_tolerance_minutes)
        if appointment.is_near_fit
        else timedelta(0)
    )
    relevant_windows = tuple(
        (window_start, window_end)
        for window_start, window_end in merged_windows
        if window_start <= appointment.end_at + tolerance
        and window_end >= appointment.start_at - tolerance
    )
    payload = {
        "version": 1,
        "appointment": str(appointment.id),
        "resource": str(appointment.booking_resource_id),
        "resource_active": appointment.booking_resource.is_active,
        "start_at": _fingerprint_datetime(appointment.start_at),
        "end_at": _fingerprint_datetime(appointment.end_at),
        "is_near_fit": appointment.is_near_fit,
        "near_fit_tolerance_minutes": near_fit_tolerance_minutes,
        "reasons": reasons,
        "availability": tuple(
            (_fingerprint_datetime(start_at), _fingerprint_datetime(end_at))
            for start_at, end_at in relevant_windows
        ),
        "blocked": tuple(
            (
                _fingerprint_datetime(blocked_time.start_at),
                _fingerprint_datetime(blocked_time.end_at),
            )
            for blocked_time in overlapping_blocks
        ),
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _fingerprint_datetime(value: datetime) -> str:
    return value.astimezone(datetime_timezone.utc).isoformat()


def _merge_availability_windows(
    windows: list[AvailabilityWindow],
) -> tuple[tuple[datetime, datetime], ...]:
    """Merge overlapping or adjacent windows before validating appointments."""

    merged: list[tuple[datetime, datetime]] = []
    for window in sorted(windows, key=lambda item: (item.start_at, item.end_at)):
        if not merged or window.start_at > merged[-1][1]:
            merged.append((window.start_at, window.end_at))
            continue
        previous_start, previous_end = merged[-1]
        merged[-1] = (previous_start, max(previous_end, window.end_at))
    return tuple(merged)


def _appointment_fits_availability(
    *,
    appointment: Appointment,
    merged_windows: tuple[tuple[datetime, datetime], ...],
    near_fit_tolerance: timedelta,
) -> bool:
    for window_start, window_end in merged_windows:
        if window_start <= appointment.start_at and appointment.end_at <= window_end:
            return True
        if (
            appointment.is_near_fit
            and window_start <= appointment.start_at < window_end
            and appointment.end_at <= window_end + near_fit_tolerance
        ):
            return True
    return False


def _local_midnight_utc(*, day: date, zone) -> datetime:
    return datetime.combine(day, time.min, tzinfo=zone).astimezone(datetime_timezone.utc)
