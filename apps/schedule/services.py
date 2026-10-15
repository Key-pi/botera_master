from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from datetime import timezone as datetime_timezone
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.services import ensure_partner_access
from apps.catalog.models import Service
from apps.partners.models import Partner
from apps.schedule.models import (
    ALLOWED_SLOT_STEPS,
    MAX_AVAILABILITY_WINDOW_LENGTH,
    MAX_BLOCKED_TIME_LENGTH,
    MAX_BOOKING_HORIZON_DAYS,
    MAX_CANCELLATION_DEADLINE_HOURS,
    MAX_NEAR_FIT_TOLERANCE_MINUTES,
    AvailabilityTemplate,
    AvailabilityTemplateWindow,
    AvailabilityWindow,
    BlockedTime,
    BookingResource,
    PartnerScheduleSettings,
    generate_advisory_lock_key,
)
from apps.schedule.selectors import (
    availability_windows_for_resource,
    blocked_times_for_resource,
)


@dataclass(frozen=True)
class TimeInterval:
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True)
class GeneratedSlot:
    start_at: datetime
    end_at: datetime
    is_near_fit: bool
    availability_window_id: object


def initialize_partner_schedule(*, partner: Partner) -> BookingResource:
    """Create the invisible solo-master resource and schedule settings for a partner."""

    settings, _settings_created = PartnerScheduleSettings.objects.get_or_create(partner=partner)
    del settings
    return get_or_create_default_booking_resource(partner=partner)


def get_or_create_default_booking_resource(*, partner: Partner) -> BookingResource:
    """Return the partner's default booking resource, creating it with retry safety."""

    existing = BookingResource.objects.filter(partner=partner, is_default=True).first()
    if existing:
        return existing

    for _attempt in range(5):
        try:
            return BookingResource.objects.create(
                partner=partner,
                name=partner.name,
                is_default=True,
                advisory_lock_key=generate_advisory_lock_key(),
            )
        except IntegrityError:
            continue

    raise ValidationError("Could not allocate a booking resource lock key.")


def update_schedule_settings(
    *,
    actor: User,
    partner: Partner,
    confirmation_mode: str | None = None,
    slot_step_minutes: int | None = None,
    booking_horizon_days: int | None = None,
    near_fit_tolerance_minutes: int | None = None,
    cancellation_deadline_hours: int | None = None,
) -> PartnerScheduleSettings:
    """Update partner-owned schedule settings through explicit validation."""

    ensure_partner_access(actor, partner)
    schedule_settings, _created = PartnerScheduleSettings.objects.get_or_create(partner=partner)
    if confirmation_mode is not None:
        if confirmation_mode not in PartnerScheduleSettings.ConfirmationMode.values:
            raise ValidationError({"confirmation_mode": "Unknown confirmation mode."})
        schedule_settings.confirmation_mode = confirmation_mode
    if slot_step_minutes is not None:
        _validate_slot_step(slot_step_minutes)
        schedule_settings.slot_step_minutes = slot_step_minutes
    if booking_horizon_days is not None:
        _validate_booking_horizon(booking_horizon_days)
        schedule_settings.booking_horizon_days = booking_horizon_days
    if near_fit_tolerance_minutes is not None:
        _validate_near_fit_tolerance(near_fit_tolerance_minutes)
        schedule_settings.near_fit_tolerance_minutes = near_fit_tolerance_minutes
    if cancellation_deadline_hours is not None:
        _validate_cancellation_deadline(cancellation_deadline_hours)
        schedule_settings.cancellation_deadline_hours = cancellation_deadline_hours
    schedule_settings.save()
    return schedule_settings


def create_availability_window(
    *,
    actor: User,
    partner: Partner,
    booking_resource: BookingResource,
    start_at: datetime,
    end_at: datetime,
    source_template: AvailabilityTemplate | None = None,
) -> AvailabilityWindow:
    """Create a concrete bookable interval in trusted partner scope."""

    ensure_partner_access(actor, partner)
    validate_availability_window_input(
        partner=partner,
        booking_resource=booking_resource,
        start_at=start_at,
        end_at=end_at,
        source_template=source_template,
    )
    return AvailabilityWindow.objects.create(
        partner=partner,
        booking_resource=booking_resource,
        start_at=start_at,
        end_at=end_at,
        source_template=source_template,
        created_by=actor,
    )


def validate_availability_window_input(
    *,
    partner: Partner,
    booking_resource: BookingResource,
    start_at: datetime,
    end_at: datetime,
    source_template: AvailabilityTemplate | None = None,
) -> None:
    """Validate one concrete window without writing it."""

    _validate_booking_resource_partner(partner=partner, booking_resource=booking_resource)
    if source_template is not None and source_template.partner_id != partner.id:
        raise ValidationError({"source_template": "Template must belong to this partner."})
    _validate_time_range(
        start_at=start_at,
        end_at=end_at,
        max_length=MAX_AVAILABILITY_WINDOW_LENGTH,
        forbid_past=True,
    )


def create_blocked_time(
    *,
    actor: User,
    partner: Partner,
    booking_resource: BookingResource,
    start_at: datetime,
    end_at: datetime,
    reason: str = "",
) -> BlockedTime:
    """Create an unavailable interval that removes generated slots."""

    ensure_partner_access(actor, partner)
    _validate_booking_resource_partner(partner=partner, booking_resource=booking_resource)
    _validate_time_range(
        start_at=start_at,
        end_at=end_at,
        max_length=MAX_BLOCKED_TIME_LENGTH,
        forbid_past=True,
    )
    return BlockedTime.objects.create(
        partner=partner,
        booking_resource=booking_resource,
        start_at=start_at,
        end_at=end_at,
        reason=reason,
        created_by=actor,
    )


def create_availability_template(
    *,
    actor: User,
    partner: Partner,
    name: str,
    is_active: bool = True,
    sort_order: int = 0,
) -> AvailabilityTemplate:
    """Create a reusable weekly availability template."""

    ensure_partner_access(actor, partner)
    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValidationError({"name": "Template name is required."})
    if sort_order < 0:
        raise ValidationError({"sort_order": "Sort order cannot be negative."})
    return AvailabilityTemplate.objects.create(
        partner=partner,
        name=cleaned_name,
        is_active=is_active,
        sort_order=sort_order,
    )


def create_availability_template_window(
    *,
    actor: User,
    template: AvailabilityTemplate,
    weekday: int,
    start_time: time,
    end_time: time,
) -> AvailabilityTemplateWindow:
    """Create one weekly local-time window inside a template."""

    ensure_partner_access(actor, template.partner)
    if weekday < 0 or weekday > 6:
        raise ValidationError({"weekday": "Weekday must be between 0 and 6."})
    if start_time >= end_time:
        raise ValidationError({"end_time": "Template windows cannot be overnight in MVP."})
    return AvailabilityTemplateWindow.objects.create(
        template=template,
        weekday=weekday,
        start_time=start_time,
        end_time=end_time,
    )


def apply_availability_template(
    *,
    actor: User,
    partner: Partner,
    template: AvailabilityTemplate,
    booking_resource: BookingResource,
    start_date: date,
    end_date: date,
) -> tuple[AvailabilityWindow, ...]:
    """Apply weekly template rules into concrete UTC availability windows."""

    ensure_partner_access(actor, partner)
    if template.partner_id != partner.id:
        raise ValidationError({"template": "Template must belong to this partner."})
    _validate_booking_resource_partner(partner=partner, booking_resource=booking_resource)
    if start_date > end_date:
        raise ValidationError({"end_date": "End date must be after start date."})

    partner_zone = ZoneInfo(partner.timezone)
    created: list[AvailabilityWindow] = []
    current_date = start_date
    with transaction.atomic():
        while current_date <= end_date:
            for template_window in template.windows.filter(weekday=current_date.weekday()):
                local_start = datetime.combine(
                    current_date,
                    template_window.start_time,
                    tzinfo=partner_zone,
                )
                local_end = datetime.combine(
                    current_date,
                    template_window.end_time,
                    tzinfo=partner_zone,
                )
                created.append(
                    create_availability_window(
                        actor=actor,
                        partner=partner,
                        booking_resource=booking_resource,
                        start_at=local_start.astimezone(datetime_timezone.utc),
                        end_at=local_end.astimezone(datetime_timezone.utc),
                        source_template=template,
                    )
                )
            current_date += timedelta(days=1)
    return tuple(created)


def duration_minutes_for_services(services: Iterable[Service]) -> int:
    """Return total duration for a multi-service booking selection."""

    total = sum(service.duration_minutes for service in services)
    if total <= 0:
        raise ValidationError({"services": "At least one positive-duration service is required."})
    return total


def generate_available_slots(
    *,
    partner: Partner,
    booking_resource: BookingResource,
    duration_minutes: int,
    range_start: datetime,
    range_end: datetime,
    schedule_settings: PartnerScheduleSettings | None = None,
    busy_intervals: Sequence[TimeInterval] = (),
    now: datetime | None = None,
) -> tuple[GeneratedSlot, ...]:
    """Generate client-visible slots from concrete windows, blocks, and busy intervals."""

    _validate_booking_resource_partner(partner=partner, booking_resource=booking_resource)
    if duration_minutes <= 0:
        raise ValidationError({"duration_minutes": "Duration must be positive."})
    _validate_aware_datetime(range_start, "range_start")
    _validate_aware_datetime(range_end, "range_end")
    if range_start >= range_end:
        raise ValidationError({"range_end": "Range end must be after range start."})

    schedule_settings = (
        schedule_settings or PartnerScheduleSettings.objects.get_or_create(partner=partner)[0]
    )
    current_time = now or timezone.now()
    _validate_aware_datetime(current_time, "now")
    horizon_end = current_time + timedelta(days=schedule_settings.booking_horizon_days)
    search_start = max(range_start, current_time)
    search_end = min(range_end, horizon_end)
    if search_start >= search_end:
        return ()

    windows = availability_windows_for_resource(
        partner=partner,
        booking_resource=booking_resource,
        start_at=search_start,
        end_at=search_end,
    )
    blocks = tuple(
        TimeInterval(block.start_at, block.end_at)
        for block in blocked_times_for_resource(
            partner=partner,
            booking_resource=booking_resource,
            start_at=search_start,
            end_at=search_end + timedelta(minutes=schedule_settings.near_fit_tolerance_minutes),
        )
    )
    duration = timedelta(minutes=duration_minutes)
    step = timedelta(minutes=schedule_settings.slot_step_minutes)
    tolerance = timedelta(minutes=schedule_settings.near_fit_tolerance_minutes)
    slots_by_interval: dict[tuple[datetime, datetime], GeneratedSlot] = {}

    for window in windows:
        candidate_start = max(window.start_at, search_start)
        while candidate_start < window.end_at and candidate_start < search_end:
            candidate_end = candidate_start + duration
            is_normal_fit = candidate_end <= window.end_at
            is_near_fit = (
                not is_normal_fit
                and tolerance > timedelta(0)
                and candidate_end <= window.end_at + tolerance
            )
            if (is_normal_fit or is_near_fit) and not _overlaps_any(
                candidate_start,
                candidate_end,
                (*blocks, *busy_intervals),
            ):
                key = (candidate_start, candidate_end)
                previous = slots_by_interval.get(key)
                if previous is None or previous.is_near_fit:
                    slots_by_interval[key] = GeneratedSlot(
                        start_at=candidate_start,
                        end_at=candidate_end,
                        is_near_fit=is_near_fit,
                        availability_window_id=window.id,
                    )
            candidate_start += step

    return tuple(sorted(slots_by_interval.values(), key=lambda slot: slot.start_at))


def _validate_time_range(
    *,
    start_at: datetime,
    end_at: datetime,
    max_length: timedelta,
    forbid_past: bool,
) -> None:
    _validate_aware_datetime(start_at, "start_at")
    _validate_aware_datetime(end_at, "end_at")
    if start_at >= end_at:
        raise ValidationError({"end_at": "End time must be after start time."})
    if end_at - start_at > max_length:
        raise ValidationError({"end_at": "Interval is too long."})
    if forbid_past and start_at < timezone.now():
        raise ValidationError({"start_at": "New schedule intervals cannot start in the past."})


def _validate_aware_datetime(value: datetime, field_name: str) -> None:
    if timezone.is_naive(value):
        raise ValidationError({field_name: "Datetime must be timezone-aware."})


def _validate_booking_resource_partner(
    *,
    partner: Partner,
    booking_resource: BookingResource,
) -> None:
    if booking_resource.partner_id != partner.id:
        raise ValidationError({"booking_resource": "Booking resource must belong to this partner."})


def _validate_slot_step(value: int) -> None:
    if value not in ALLOWED_SLOT_STEPS:
        raise ValidationError({"slot_step_minutes": "Unsupported slot step."})


def _validate_booking_horizon(value: int) -> None:
    if value < 1 or value > MAX_BOOKING_HORIZON_DAYS:
        raise ValidationError({"booking_horizon_days": "Booking horizon is out of bounds."})


def _validate_near_fit_tolerance(value: int) -> None:
    if value < 0 or value > MAX_NEAR_FIT_TOLERANCE_MINUTES:
        raise ValidationError(
            {"near_fit_tolerance_minutes": "Near-fit tolerance is out of bounds."}
        )


def _validate_cancellation_deadline(value: int) -> None:
    if value < 0 or value > MAX_CANCELLATION_DEADLINE_HOURS:
        raise ValidationError(
            {"cancellation_deadline_hours": "Cancellation deadline is out of bounds."}
        )


def _overlaps_any(
    start_at: datetime,
    end_at: datetime,
    intervals: Sequence[TimeInterval],
) -> bool:
    return any(start_at < interval.end_at and end_at > interval.start_at for interval in intervals)
