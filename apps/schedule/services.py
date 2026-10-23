from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from datetime import timezone as datetime_timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.services import ensure_partner_access
from apps.appointments.models import Appointment
from apps.audit.services import record_audit_event
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
    ScheduleConflictAcknowledgement,
    generate_advisory_lock_key,
)
from apps.schedule.selectors import (
    availability_windows_for_resource,
    availability_windows_starting_in_period,
    blocked_times_for_resource,
    blocked_times_starting_in_period,
    schedule_conflicts_for_partner_period,
)

MAX_SCHEDULE_CONFLICT_BULK_SIZE = 100


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


@dataclass(frozen=True)
class ScheduleCopyResult:
    """Counts produced by one idempotent calendar copy operation."""

    availability_created: int
    blocked_created: int
    skipped_existing: int

    @property
    def created_total(self) -> int:
        return self.availability_created + self.blocked_created


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
    require_phone_for_booking: bool | None = None,
) -> PartnerScheduleSettings:
    """Update partner-owned schedule settings through explicit validation."""

    ensure_partner_access(actor, partner)
    schedule_settings, _created = PartnerScheduleSettings.objects.get_or_create(partner=partner)
    if confirmation_mode is not None:
        validate_confirmation_mode(confirmation_mode)
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
    if require_phone_for_booking is not None:
        _validate_require_phone_for_booking(require_phone_for_booking)
        schedule_settings.require_phone_for_booking = require_phone_for_booking
    schedule_settings.save()
    return schedule_settings


def validate_schedule_settings_input(
    *,
    confirmation_mode: str,
    slot_step_minutes: int,
    booking_horizon_days: int,
    near_fit_tolerance_minutes: int,
    cancellation_deadline_hours: int,
    require_phone_for_booking: bool,
) -> None:
    """Validate a complete partner schedule settings form without writing it."""

    validate_confirmation_mode(confirmation_mode)
    _validate_slot_step(slot_step_minutes)
    _validate_booking_horizon(booking_horizon_days)
    _validate_near_fit_tolerance(near_fit_tolerance_minutes)
    _validate_cancellation_deadline(cancellation_deadline_hours)
    _validate_require_phone_for_booking(require_phone_for_booking)


def validate_confirmation_mode(value: str) -> None:
    if value not in PartnerScheduleSettings.ConfirmationMode.values:
        raise ValidationError({"confirmation_mode": "Unknown confirmation mode."})


def _validate_require_phone_for_booking(value: bool) -> None:
    if not isinstance(value, bool):
        raise ValidationError(
            {"require_phone_for_booking": "Require phone for booking must be true or false."}
        )


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
    validate_blocked_time_input(
        partner=partner,
        booking_resource=booking_resource,
        start_at=start_at,
        end_at=end_at,
    )
    return BlockedTime.objects.create(
        partner=partner,
        booking_resource=booking_resource,
        start_at=start_at,
        end_at=end_at,
        reason=reason,
        created_by=actor,
    )


def update_blocked_time(
    *,
    actor: User,
    blocked_time: BlockedTime,
    booking_resource: BookingResource,
    start_at: datetime,
    end_at: datetime,
    reason: str = "",
) -> BlockedTime:
    """Update one blocked interval without bypassing partner/range checks."""

    ensure_partner_access(actor, blocked_time.partner)
    validate_blocked_time_input(
        partner=blocked_time.partner,
        booking_resource=booking_resource,
        start_at=start_at,
        end_at=end_at,
    )
    blocked_time.booking_resource = booking_resource
    blocked_time.start_at = start_at
    blocked_time.end_at = end_at
    blocked_time.reason = reason
    blocked_time.save(
        update_fields=[
            "booking_resource",
            "start_at",
            "end_at",
            "reason",
            "updated_at",
        ]
    )
    return blocked_time


def delete_blocked_time(*, actor: User, blocked_time: BlockedTime) -> None:
    """Delete one partner-owned blocked interval."""

    ensure_partner_access(actor, blocked_time.partner)
    blocked_time.delete()


def acknowledge_schedule_conflicts(
    *,
    actor: User,
    partner: Partner,
    appointment_ids: Sequence[UUID],
    now: datetime | None = None,
) -> tuple[ScheduleConflictAcknowledgement, ...]:
    """Keep selected active conflicts as-is for their current schedule facts."""

    ensure_partner_access(actor, partner)
    normalized_ids = _normalize_conflict_appointment_ids(appointment_ids)
    current_time = now or timezone.now()
    with transaction.atomic():
        appointments = tuple(
            Appointment.objects.select_for_update()
            .select_related("partner", "booking_resource")
            .filter(
                partner=partner,
                id__in=normalized_ids,
                status=Appointment.Status.CONFIRMED,
                end_at__gt=current_time,
            )
            .order_by("start_at", "created_at")
        )
        if len(appointments) != len(normalized_ids):
            raise ValidationError(
                {"appointments": "Select current confirmed appointments from this partner."}
            )

        conflicts = schedule_conflicts_for_partner_period(
            partner=partner,
            start_at=min(current_time, min(appointment.start_at for appointment in appointments)),
            end_at=max(appointment.end_at for appointment in appointments)
            + timedelta(microseconds=1),
            include_acknowledged=True,
        )
        conflicts_by_appointment = {
            conflict.appointment.id: conflict for conflict in conflicts
        }
        if any(appointment_id not in conflicts_by_appointment for appointment_id in normalized_ids):
            raise ValidationError(
                {"appointments": "One or more selected schedule conflicts are no longer active."}
            )

        acknowledgements: list[ScheduleConflictAcknowledgement] = []
        for appointment in appointments:
            conflict = conflicts_by_appointment[appointment.id]
            acknowledgement, _created = ScheduleConflictAcknowledgement.objects.update_or_create(
                appointment=appointment,
                defaults={
                    "partner": partner,
                    "fingerprint": conflict.fingerprint,
                    "acknowledged_by": actor,
                },
            )
            acknowledgements.append(acknowledgement)

        record_audit_event(
            partner=partner,
            actor=actor,
            event_type="schedule.conflicts_kept_as_is",
            target_type="schedule_conflict",
            message=f"Kept {len(acknowledgements)} schedule conflict(s) as is.",
            payload={
                "appointment_ids": [str(appointment.id) for appointment in appointments],
                "count": len(acknowledgements),
            },
        )
    return tuple(acknowledgements)


def reopen_schedule_conflicts(
    *,
    actor: User,
    partner: Partner,
    appointment_ids: Sequence[UUID],
) -> int:
    """Remove current keep-as-is decisions so conflicts return to active review."""

    ensure_partner_access(actor, partner)
    normalized_ids = _normalize_conflict_appointment_ids(appointment_ids)
    with transaction.atomic():
        acknowledgements = tuple(
            ScheduleConflictAcknowledgement.objects.select_for_update()
            .filter(partner=partner, appointment_id__in=normalized_ids)
            .order_by("appointment_id")
        )
        if len(acknowledgements) != len(normalized_ids):
            raise ValidationError(
                {"appointments": "Select conflicts currently kept as is for this partner."}
            )
        ScheduleConflictAcknowledgement.objects.filter(
            id__in=(acknowledgement.id for acknowledgement in acknowledgements)
        ).delete()
        record_audit_event(
            partner=partner,
            actor=actor,
            event_type="schedule.conflicts_reopened",
            target_type="schedule_conflict",
            message=f"Reopened {len(acknowledgements)} schedule conflict(s).",
            payload={
                "appointment_ids": [
                    str(acknowledgement.appointment_id)
                    for acknowledgement in acknowledgements
                ],
                "count": len(acknowledgements),
            },
        )
    return len(acknowledgements)


def _normalize_conflict_appointment_ids(
    appointment_ids: Sequence[UUID],
) -> tuple[UUID, ...]:
    normalized_ids = tuple(dict.fromkeys(appointment_ids))
    if not normalized_ids:
        raise ValidationError({"appointments": "Select at least one schedule conflict."})
    if len(normalized_ids) > MAX_SCHEDULE_CONFLICT_BULK_SIZE:
        raise ValidationError(
            {
                "appointments": (
                    f"Select no more than {MAX_SCHEDULE_CONFLICT_BULK_SIZE} conflicts at once."
                )
            }
        )
    return normalized_ids


def validate_blocked_time_input(
    *,
    partner: Partner,
    booking_resource: BookingResource,
    start_at: datetime,
    end_at: datetime,
) -> None:
    """Validate one blocked interval without writing it."""

    _validate_booking_resource_partner(partner=partner, booking_resource=booking_resource)
    _validate_time_range(
        start_at=start_at,
        end_at=end_at,
        max_length=MAX_BLOCKED_TIME_LENGTH,
        forbid_past=True,
    )


def copy_schedule_day(
    *,
    actor: User,
    partner: Partner,
    booking_resource: BookingResource,
    source_day: date,
    target_day: date,
) -> ScheduleCopyResult:
    """Copy one resource's concrete day plan while preserving local wall-clock time."""

    return _copy_schedule_period(
        actor=actor,
        partner=partner,
        booking_resource=booking_resource,
        source_start_date=source_day,
        target_start_date=target_day,
        day_count=1,
    )


def copy_schedule_week(
    *,
    actor: User,
    partner: Partner,
    booking_resource: BookingResource,
    source_week_start: date,
    target_week_start: date,
) -> ScheduleCopyResult:
    """Copy one resource's concrete week plan into another partner-local week."""

    normalized_source = source_week_start - timedelta(days=source_week_start.weekday())
    normalized_target = target_week_start - timedelta(days=target_week_start.weekday())
    return _copy_schedule_period(
        actor=actor,
        partner=partner,
        booking_resource=booking_resource,
        source_start_date=normalized_source,
        target_start_date=normalized_target,
        day_count=7,
    )


def _copy_schedule_period(
    *,
    actor: User,
    partner: Partner,
    booking_resource: BookingResource,
    source_start_date: date,
    target_start_date: date,
    day_count: int,
) -> ScheduleCopyResult:
    """Copy schedule facts starting in a bounded local period, never appointments."""

    ensure_partner_access(actor, partner)
    _validate_booking_resource_partner(partner=partner, booking_resource=booking_resource)
    if day_count not in (1, 7):
        raise ValidationError("Schedule copy supports only one day or one week.")
    if source_start_date == target_start_date:
        raise ValidationError({"target_date": "Choose a different target period."})
    if source_start_date > date.max - timedelta(days=day_count):
        raise ValidationError({"source_date": "Source period is out of supported range."})

    partner_zone = ZoneInfo(partner.timezone)
    source_start_at = _local_midnight_utc(day=source_start_date, zone=partner_zone)
    source_end_at = _local_midnight_utc(
        day=source_start_date + timedelta(days=day_count),
        zone=partner_zone,
    )
    day_delta = (target_start_date - source_start_date).days
    availability_created = 0
    blocked_created = 0
    skipped_existing = 0

    with transaction.atomic():
        locked_resource = BookingResource.objects.select_for_update().get(id=booking_resource.id)
        _validate_booking_resource_partner(partner=partner, booking_resource=locked_resource)
        source_windows = tuple(
            availability_windows_starting_in_period(
                partner=partner,
                booking_resource=locked_resource,
                start_at=source_start_at,
                end_at=source_end_at,
            )
        )
        source_blocks = tuple(
            blocked_times_starting_in_period(
                partner=partner,
                booking_resource=locked_resource,
                start_at=source_start_at,
                end_at=source_end_at,
            )
        )

        for source_window in source_windows:
            target_start_at, target_end_at = _shift_interval_by_local_days(
                start_at=source_window.start_at,
                end_at=source_window.end_at,
                day_delta=day_delta,
                zone=partner_zone,
            )
            validate_availability_window_input(
                partner=partner,
                booking_resource=locked_resource,
                start_at=target_start_at,
                end_at=target_end_at,
            )
            if AvailabilityWindow.objects.filter(
                partner=partner,
                booking_resource=locked_resource,
                start_at=target_start_at,
                end_at=target_end_at,
            ).exists():
                skipped_existing += 1
                continue
            create_availability_window(
                actor=actor,
                partner=partner,
                booking_resource=locked_resource,
                start_at=target_start_at,
                end_at=target_end_at,
            )
            availability_created += 1

        for source_block in source_blocks:
            target_start_at, target_end_at = _shift_interval_by_local_days(
                start_at=source_block.start_at,
                end_at=source_block.end_at,
                day_delta=day_delta,
                zone=partner_zone,
            )
            validate_blocked_time_input(
                partner=partner,
                booking_resource=locked_resource,
                start_at=target_start_at,
                end_at=target_end_at,
            )
            if BlockedTime.objects.filter(
                partner=partner,
                booking_resource=locked_resource,
                start_at=target_start_at,
                end_at=target_end_at,
                reason=source_block.reason,
            ).exists():
                skipped_existing += 1
                continue
            create_blocked_time(
                actor=actor,
                partner=partner,
                booking_resource=locked_resource,
                start_at=target_start_at,
                end_at=target_end_at,
                reason=source_block.reason,
            )
            blocked_created += 1

    return ScheduleCopyResult(
        availability_created=availability_created,
        blocked_created=blocked_created,
        skipped_existing=skipped_existing,
    )


def _shift_interval_by_local_days(
    *,
    start_at: datetime,
    end_at: datetime,
    day_delta: int,
    zone: ZoneInfo,
) -> tuple[datetime, datetime]:
    """Shift both local endpoints by calendar days instead of UTC duration."""

    local_start = start_at.astimezone(zone)
    local_end = end_at.astimezone(zone)
    return (
        _shift_local_endpoint(value=local_start, day_delta=day_delta, zone=zone),
        _shift_local_endpoint(value=local_end, day_delta=day_delta, zone=zone),
    )


def _shift_local_endpoint(*, value: datetime, day_delta: int, zone: ZoneInfo) -> datetime:
    try:
        shifted = datetime.combine(
            value.date() + timedelta(days=day_delta),
            value.timetz(),
        )
    except OverflowError as exc:
        raise ValidationError({"target_date": "Target period is out of supported range."}) from exc

    shifted_utc = shifted.astimezone(datetime_timezone.utc)
    round_trip = shifted_utc.astimezone(zone)
    if round_trip.replace(tzinfo=None) != shifted.replace(tzinfo=None):
        raise ValidationError(
            {"target_date": "Target period contains a local time skipped by a timezone change."}
        )
    return shifted_utc


def _local_midnight_utc(*, day: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=zone).astimezone(datetime_timezone.utc)


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
    cleaned_name = validate_availability_template_input(name=name, sort_order=sort_order)
    return AvailabilityTemplate.objects.create(
        partner=partner,
        name=cleaned_name,
        is_active=is_active,
        sort_order=sort_order,
    )


def update_availability_template(
    *,
    actor: User,
    template: AvailabilityTemplate,
    name: str,
    is_active: bool,
    sort_order: int,
) -> AvailabilityTemplate:
    """Update reusable template metadata without changing generated windows."""

    ensure_partner_access(actor, template.partner)
    template.name = validate_availability_template_input(name=name, sort_order=sort_order)
    template.is_active = is_active
    template.sort_order = sort_order
    template.save(update_fields=["name", "is_active", "sort_order", "updated_at"])
    return template


def delete_availability_template(*, actor: User, template: AvailabilityTemplate) -> None:
    """Delete a template while preserving concrete windows through SET_NULL."""

    ensure_partner_access(actor, template.partner)
    template.delete()


def validate_availability_template_input(*, name: str, sort_order: int) -> str:
    """Validate reusable template metadata and return the normalized name."""

    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValidationError({"name": "Template name is required."})
    if sort_order < 0:
        raise ValidationError({"sort_order": "Sort order cannot be negative."})
    return cleaned_name


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
    validate_availability_template_window_input(
        weekday=weekday,
        start_time=start_time,
        end_time=end_time,
    )
    return AvailabilityTemplateWindow.objects.create(
        template=template,
        weekday=weekday,
        start_time=start_time,
        end_time=end_time,
    )


def update_availability_template_window(
    *,
    actor: User,
    template_window: AvailabilityTemplateWindow,
    template: AvailabilityTemplate,
    weekday: int,
    start_time: time,
    end_time: time,
) -> AvailabilityTemplateWindow:
    """Update one template rule within trusted partner scope."""

    with transaction.atomic():
        stored_window = (
            AvailabilityTemplateWindow.objects.select_for_update()
            .select_related("template__partner")
            .get(id=template_window.id)
        )
        ensure_partner_access(actor, stored_window.template.partner)
        ensure_partner_access(actor, template.partner)
        if template.partner_id != stored_window.template.partner_id:
            raise ValidationError({"template": "Template must belong to the same partner."})
        validate_availability_template_window_input(
            weekday=weekday,
            start_time=start_time,
            end_time=end_time,
        )
        stored_window.template = template
        stored_window.weekday = weekday
        stored_window.start_time = start_time
        stored_window.end_time = end_time
        stored_window.save(update_fields=["template", "weekday", "start_time", "end_time"])
    return stored_window


def delete_availability_template_window(
    *,
    actor: User,
    template_window: AvailabilityTemplateWindow,
) -> None:
    """Delete one partner-owned weekly template rule."""

    ensure_partner_access(actor, template_window.template.partner)
    template_window.delete()


def validate_availability_template_window_input(
    *,
    weekday: int,
    start_time: time,
    end_time: time,
) -> None:
    """Validate one weekly local-time rule without writing it."""

    if weekday < 0 or weekday > 6:
        raise ValidationError({"weekday": "Weekday must be between 0 and 6."})
    if start_time >= end_time:
        raise ValidationError({"end_time": "Template windows cannot be overnight in MVP."})


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
    created: list[AvailabilityWindow] = []
    with transaction.atomic():
        template = AvailabilityTemplate.objects.select_for_update().get(id=template.id)
        validate_availability_template_application(
            partner=partner,
            template=template,
            booking_resource=booking_resource,
            start_date=start_date,
            end_date=end_date,
        )
        partner_zone = ZoneInfo(partner.timezone)
        current_date = start_date
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
                start_at = local_start.astimezone(datetime_timezone.utc)
                end_at = local_end.astimezone(datetime_timezone.utc)
                if AvailabilityWindow.objects.filter(
                    partner=partner,
                    booking_resource=booking_resource,
                    source_template=template,
                    start_at=start_at,
                    end_at=end_at,
                ).exists():
                    continue
                created.append(
                    create_availability_window(
                        actor=actor,
                        partner=partner,
                        booking_resource=booking_resource,
                        start_at=start_at,
                        end_at=end_at,
                        source_template=template,
                    )
                )
            current_date += timedelta(days=1)
    return tuple(created)


def validate_availability_template_application(
    *,
    partner: Partner,
    template: AvailabilityTemplate,
    booking_resource: BookingResource,
    start_date: date,
    end_date: date,
) -> None:
    """Validate a future template application without creating windows."""

    if template.partner_id != partner.id:
        raise ValidationError({"template": "Template must belong to this partner."})
    if not template.is_active:
        raise ValidationError({"template": "Only active templates can be applied."})
    _validate_booking_resource_partner(partner=partner, booking_resource=booking_resource)
    if start_date > end_date:
        raise ValidationError({"end_date": "End date must be after start date."})
    partner_zone = ZoneInfo(partner.timezone)
    if start_date < timezone.localdate(timezone=partner_zone):
        raise ValidationError({"start_date": "Templates cannot be applied to past dates."})
    if (end_date - start_date).days + 1 > MAX_BOOKING_HORIZON_DAYS:
        raise ValidationError(
            {"end_date": f"Template range cannot exceed {MAX_BOOKING_HORIZON_DAYS} days."}
        )


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
    schedule_windows: Sequence[AvailabilityWindow] | None = None,
    blocked_intervals: Sequence[TimeInterval] | None = None,
    now: datetime | None = None,
) -> tuple[GeneratedSlot, ...]:
    """Generate slots, optionally reusing schedule data loaded for a wider range."""

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

    duration = timedelta(minutes=duration_minutes)
    step = timedelta(minutes=schedule_settings.slot_step_minutes)
    tolerance = timedelta(minutes=schedule_settings.near_fit_tolerance_minutes)
    overlap_lookahead = duration + tolerance
    if schedule_windows is None:
        windows = tuple(
            availability_windows_for_resource(
                partner=partner,
                booking_resource=booking_resource,
                start_at=search_start,
                end_at=search_end,
            )
        )
    else:
        windows = tuple(
            window
            for window in schedule_windows
            if window.partner_id == partner.id
            and window.booking_resource_id == booking_resource.id
            and window.start_at < search_end
            and window.end_at > search_start
        )

    if blocked_intervals is None:
        blocks = tuple(
            TimeInterval(block.start_at, block.end_at)
            for block in blocked_times_for_resource(
                partner=partner,
                booking_resource=booking_resource,
                start_at=search_start,
                end_at=search_end + overlap_lookahead,
            )
        )
    else:
        blocks = tuple(
            interval
            for interval in blocked_intervals
            if interval.start_at < search_end + overlap_lookahead and interval.end_at > search_start
        )
    relevant_busy_intervals = tuple(
        interval
        for interval in busy_intervals
        if interval.start_at < search_end + overlap_lookahead and interval.end_at > search_start
    )
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
                (*blocks, *relevant_busy_intervals),
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
