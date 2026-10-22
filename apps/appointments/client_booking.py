from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from datetime import timezone as datetime_timezone
from typing import TYPE_CHECKING
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.services import ensure_partner_access
from apps.appointments.models import Appointment, AppointmentProposal
from apps.appointments.proposals import (
    AppointmentProposalOptionInput,
    create_appointment_proposal,
)
from apps.appointments.selectors import (
    appointments_for_client,
    confirmed_busy_intervals_for_resource,
)
from apps.appointments.services import (
    AppointmentServiceItemInput,
    acquire_booking_resource_advisory_lock_if_supported,
    cancel_requested_appointment_by_client,
    create_confirmed_appointment,
    create_requested_appointment,
    refresh_confirmed_notifications_after_commit,
)
from apps.audit.services import record_audit_event
from apps.bot.pagination import (
    DEFAULT_TELEGRAM_ENTITY_PAGE_SIZE,
    Page,
    paginate_telegram_entities,
)
from apps.catalog.models import DEFAULT_CURRENCY, Service, ServiceCategory
from apps.catalog.selectors import active_categories_for_booking, active_services_for_booking
from apps.schedule.models import PartnerScheduleSettings
from apps.schedule.selectors import (
    availability_windows_for_resource,
    blocked_times_for_resource,
)
from apps.schedule.services import (
    GeneratedSlot,
    TimeInterval,
    generate_available_slots,
    get_or_create_default_booking_resource,
)
from core.timezones import get_zone, to_partner_time

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.clients.models import ClientProfile
    from apps.partners.models import Partner
    from apps.schedule.models import BookingResource


BOOKING_DATES_PAGE_SIZE = 7
BOOKING_MONTHS_PAGE_SIZE = 6
BOOKING_SLOTS_PAGE_SIZE = 8
CLIENT_HISTORY_PAGE_SIZE = 5


@dataclass(frozen=True, slots=True)
class BookingServiceLine:
    """Client-visible snapshot preview for a selected service."""

    service_id: UUID
    category_id: UUID
    category_name: str
    name: str
    description: str
    price_amount: int
    currency: str
    duration_minutes: int


@dataclass(frozen=True, slots=True)
class BookingSummary:
    """Validated booking selection summary before slot/date selection."""

    service_lines: tuple[BookingServiceLine, ...]
    service_inputs: tuple[AppointmentServiceItemInput, ...]
    duration_minutes: int
    service_total_amount: int
    currency: str


@dataclass(frozen=True, slots=True)
class BookingDateOption:
    """A local date that has at least one available slot."""

    local_date: date
    slot_count: int
    first_slot_at: datetime
    has_near_fit: bool


@dataclass(frozen=True, slots=True)
class BookingMonthOption:
    """A local calendar month containing at least one available booking date."""

    month_start: date
    available_date_count: int


@dataclass(frozen=True, slots=True)
class BookingSlotOption:
    """A concrete client-selectable slot."""

    start_at: datetime
    end_at: datetime
    is_near_fit: bool


@dataclass(frozen=True, slots=True)
class ClientBookingResult:
    """Result of final client confirmation."""

    appointment: Appointment
    requires_master_confirmation: bool
    is_near_fit: bool


@dataclass(frozen=True, slots=True)
class ClientRescheduleResult:
    """Result of a client request to move an existing confirmed appointment."""

    appointment: Appointment
    proposal: AppointmentProposal | None
    previous_start_at: datetime
    requires_master_confirmation: bool
    is_near_fit: bool


@dataclass(frozen=True, slots=True)
class ClientProfileOverview:
    """Small client personal-area summary."""

    client_profile: ClientProfile
    visit_count: int
    future_appointments: Page[Appointment]


def booking_category_page(
    *,
    partner: Partner,
    page: int = 1,
    per_page: int = DEFAULT_TELEGRAM_ENTITY_PAGE_SIZE,
) -> Page[ServiceCategory]:
    """Return active booking categories in Telegram-safe pages."""

    categories = tuple(active_categories_for_booking(partner))
    return paginate_telegram_entities(categories, page=page, per_page=per_page)


def booking_services_page(
    *,
    partner: Partner,
    category: ServiceCategory,
    page: int = 1,
    per_page: int = DEFAULT_TELEGRAM_ENTITY_PAGE_SIZE,
) -> Page[Service]:
    """Return active services for one selected category."""

    _validate_category_available_for_booking(partner=partner, category=category)
    services = tuple(
        active_services_for_booking(partner)
        .filter(category=category)
        .order_by("sort_order", "name")
    )
    return paginate_telegram_entities(services, page=page, per_page=per_page)


def build_booking_summary(
    *,
    partner: Partner,
    selected_service_ids: Iterable[UUID | str],
) -> BookingSummary:
    """Validate selected services and build a client-visible summary."""

    services = _active_services_for_selection(
        partner=partner,
        selected_service_ids=selected_service_ids,
    )
    service_lines = tuple(_service_line(service) for service in services)
    service_total_amount = sum(line.price_amount for line in service_lines)
    duration_minutes = sum(line.duration_minutes for line in service_lines)
    currency = _single_currency(line.currency for line in service_lines)

    return BookingSummary(
        service_lines=service_lines,
        service_inputs=tuple(AppointmentServiceItemInput(service=service) for service in services),
        duration_minutes=duration_minutes,
        service_total_amount=service_total_amount,
        currency=currency,
    )


def available_booking_dates(
    *,
    partner: Partner,
    selected_service_ids: Iterable[UUID | str],
    start_date: date,
    days: int,
    booking_resource: BookingResource | None = None,
    page: int = 1,
    per_page: int = BOOKING_DATES_PAGE_SIZE,
    now: datetime | None = None,
) -> Page[BookingDateOption]:
    """Return local dates that have slots for the current service selection."""

    if days <= 0:
        raise ValidationError({"days": "Search window must be positive."})

    options = _available_booking_date_options(
        partner=partner,
        selected_service_ids=selected_service_ids,
        start_date=start_date,
        days=days,
        booking_resource=booking_resource,
        now=now,
    )
    return paginate_telegram_entities(options, page=page, per_page=per_page)


def available_booking_months(
    *,
    partner: Partner,
    selected_service_ids: Iterable[UUID | str],
    start_date: date,
    days: int,
    booking_resource: BookingResource | None = None,
    page: int = 1,
    per_page: int = BOOKING_MONTHS_PAGE_SIZE,
    now: datetime | None = None,
) -> Page[BookingMonthOption]:
    """Return months that contain at least one available date in the horizon."""

    if days <= 0:
        raise ValidationError({"days": "Search window must be positive."})
    date_options = _available_booking_date_options(
        partner=partner,
        selected_service_ids=selected_service_ids,
        start_date=start_date,
        days=days,
        booking_resource=booking_resource,
        now=now,
    )
    date_counts: dict[date, int] = {}
    for option in date_options:
        month_start = option.local_date.replace(day=1)
        date_counts[month_start] = date_counts.get(month_start, 0) + 1
    months = tuple(
        BookingMonthOption(month_start=month_start, available_date_count=count)
        for month_start, count in sorted(date_counts.items())
    )
    return paginate_telegram_entities(months, page=page, per_page=per_page)


def available_booking_slots(
    *,
    partner: Partner,
    selected_service_ids: Iterable[UUID | str],
    local_date: date,
    booking_resource: BookingResource | None = None,
    page: int = 1,
    per_page: int = BOOKING_SLOTS_PAGE_SIZE,
    now: datetime | None = None,
) -> Page[BookingSlotOption]:
    """Return concrete slots for a selected local date."""

    booking_resource = booking_resource or get_or_create_default_booking_resource(partner=partner)
    summary = build_booking_summary(partner=partner, selected_service_ids=selected_service_ids)
    slots = _slot_options_for_local_date(
        partner=partner,
        booking_resource=booking_resource,
        duration_minutes=summary.duration_minutes,
        local_date=local_date,
        now=now,
    )
    return paginate_telegram_entities(slots, page=page, per_page=per_page)


def confirm_client_booking(
    *,
    partner: Partner,
    client_profile: ClientProfile,
    selected_service_ids: Iterable[UUID | str],
    start_at: datetime,
    booking_resource: BookingResource | None = None,
    client_comment: str = "",
    now: datetime | None = None,
) -> ClientBookingResult:
    """Create requested/confirmed appointment after final client confirmation."""

    if client_profile.partner_id != partner.id:
        raise ValidationError({"client_profile": "Client profile must belong to this partner."})
    _validate_aware_datetime(start_at, "start_at")

    booking_resource = booking_resource or get_or_create_default_booking_resource(partner=partner)
    summary = build_booking_summary(partner=partner, selected_service_ids=selected_service_ids)
    schedule_settings = _schedule_settings_for_booking(partner)
    if schedule_settings.require_phone_for_booking and not client_profile.phone.strip():
        raise ValidationError({"phone": "Phone is required before booking."})
    local_date = to_partner_time(start_at, partner.timezone).date()
    matching_slot = _find_matching_slot(
        partner=partner,
        booking_resource=booking_resource,
        summary=summary,
        local_date=local_date,
        start_at=start_at,
        now=now,
    )

    if (
        matching_slot.is_near_fit
        or schedule_settings.confirmation_mode == PartnerScheduleSettings.ConfirmationMode.MANUAL
    ):
        appointment = create_requested_appointment(
            actor=None,
            partner=partner,
            booking_resource=booking_resource,
            client_profile=client_profile,
            start_at=matching_slot.start_at,
            service_items=summary.service_inputs,
            client_comment=client_comment,
            is_near_fit=matching_slot.is_near_fit,
            confirmation_mode_snapshot=schedule_settings.confirmation_mode,
        )
    else:
        appointment = create_confirmed_appointment(
            actor=None,
            partner=partner,
            booking_resource=booking_resource,
            client_profile=client_profile,
            start_at=matching_slot.start_at,
            service_items=summary.service_inputs,
            client_comment=client_comment,
            confirmation_mode_snapshot=schedule_settings.confirmation_mode,
        )

    return ClientBookingResult(
        appointment=appointment,
        requires_master_confirmation=appointment.status == Appointment.Status.REQUESTED,
        is_near_fit=matching_slot.is_near_fit,
    )


def available_client_reschedule_dates(
    *,
    partner: Partner,
    client_profile: ClientProfile,
    appointment: Appointment,
    start_date: date,
    days: int,
    page: int = 1,
    per_page: int = BOOKING_DATES_PAGE_SIZE,
    now: datetime | None = None,
) -> Page[BookingDateOption]:
    """Return dates where the existing appointment snapshots fit another slot."""

    if days <= 0:
        raise ValidationError({"days": "Search window must be positive."})
    current_time = now or timezone.now()
    _validate_client_reschedule(
        partner=partner,
        client_profile=client_profile,
        appointment=appointment,
        now=current_time,
    )
    options = _available_date_options_for_duration(
        partner=partner,
        booking_resource=appointment.booking_resource,
        duration_minutes=_appointment_duration_minutes(appointment),
        start_date=start_date,
        days=days,
        exclude_appointment=appointment,
        excluded_start_ats=frozenset({appointment.start_at}),
        now=current_time,
    )
    return paginate_telegram_entities(options, page=page, per_page=per_page)


def available_client_reschedule_slots(
    *,
    partner: Partner,
    client_profile: ClientProfile,
    appointment: Appointment,
    local_date: date,
    page: int = 1,
    per_page: int = BOOKING_SLOTS_PAGE_SIZE,
    now: datetime | None = None,
) -> Page[BookingSlotOption]:
    """Return slots for moving one client-owned confirmed appointment."""

    current_time = now or timezone.now()
    _validate_client_reschedule(
        partner=partner,
        client_profile=client_profile,
        appointment=appointment,
        now=current_time,
    )
    slots = _reschedule_slot_options_for_local_date(
        appointment=appointment,
        duration_minutes=_appointment_duration_minutes(appointment),
        local_date=local_date,
        now=current_time,
    )
    return paginate_telegram_entities(slots, page=page, per_page=per_page)


def available_requested_alternative_dates(
    *,
    actor: User,
    partner: Partner,
    appointment: Appointment,
    start_date: date,
    days: int,
    page: int = 1,
    per_page: int = BOOKING_DATES_PAGE_SIZE,
    now: datetime | None = None,
) -> Page[BookingDateOption]:
    """Return dates a partner can offer for one requested appointment."""

    if days <= 0:
        raise ValidationError({"days": "Search window must be positive."})
    current_time = now or timezone.now()
    _validate_requested_alternative_access(
        actor=actor,
        partner=partner,
        appointment=appointment,
    )
    options = _available_date_options_for_duration(
        partner=partner,
        booking_resource=appointment.booking_resource,
        duration_minutes=_appointment_duration_minutes(appointment),
        start_date=start_date,
        days=days,
        excluded_start_ats=frozenset({appointment.start_at}),
        now=current_time,
    )
    return paginate_telegram_entities(options, page=page, per_page=per_page)


def available_requested_alternative_slots(
    *,
    actor: User,
    partner: Partner,
    appointment: Appointment,
    local_date: date,
    page: int = 1,
    per_page: int = BOOKING_SLOTS_PAGE_SIZE,
    now: datetime | None = None,
) -> Page[BookingSlotOption]:
    """Return concrete slots a partner can offer for one request."""

    current_time = now or timezone.now()
    _validate_requested_alternative_access(
        actor=actor,
        partner=partner,
        appointment=appointment,
    )
    slots = _requested_alternative_slot_options_for_local_date(
        appointment=appointment,
        local_date=local_date,
        now=current_time,
    )
    return paginate_telegram_entities(slots, page=page, per_page=per_page)


def create_requested_alternative_proposal(
    *,
    actor: User,
    partner: Partner,
    appointment: Appointment,
    selected_start_ats: Iterable[datetime],
    now: datetime | None = None,
) -> AppointmentProposal:
    """Create a requested-alternatives proposal from freshly validated slots."""

    current_time = now or timezone.now()
    start_ats = _unique_aware_datetimes(selected_start_ats)
    if not start_ats:
        raise ValidationError({"options": "Select at least one alternative time."})

    with transaction.atomic():
        appointment = (
            Appointment.objects.select_for_update()
            .select_related("partner", "booking_resource", "client_profile")
            .prefetch_related("service_items")
            .get(id=appointment.id)
        )
        _validate_requested_alternative_access(
            actor=actor,
            partner=partner,
            appointment=appointment,
        )
        option_inputs = []
        for start_at in start_ats:
            matching_slot = _find_matching_requested_alternative_slot(
                appointment=appointment,
                start_at=start_at,
                now=current_time,
            )
            option_inputs.append(
                AppointmentProposalOptionInput(
                    start_at=matching_slot.start_at,
                    is_near_fit=matching_slot.is_near_fit,
                )
            )
        return create_appointment_proposal(
            actor=actor,
            appointment=appointment,
            proposal_type=AppointmentProposal.Type.REQUESTED_ALTERNATIVES,
            options=option_inputs,
        )


def reschedule_client_appointment(
    *,
    partner: Partner,
    client_profile: ClientProfile,
    appointment: Appointment,
    start_at: datetime,
    now: datetime | None = None,
) -> ClientRescheduleResult:
    """Move immediately or create a master-confirmed proposal per partner settings."""

    _validate_aware_datetime(start_at, "start_at")
    current_time = now or timezone.now()
    _validate_client_reschedule(
        partner=partner,
        client_profile=client_profile,
        appointment=appointment,
        now=current_time,
    )
    matching_slot = _find_matching_reschedule_slot(
        appointment=appointment,
        start_at=start_at,
        now=current_time,
    )
    schedule_settings = _schedule_settings_for_booking(partner)
    previous_start_at = appointment.start_at

    if (
        matching_slot.is_near_fit
        or schedule_settings.confirmation_mode == PartnerScheduleSettings.ConfirmationMode.MANUAL
    ):
        proposal = create_appointment_proposal(
            actor=None,
            appointment=appointment,
            proposal_type=AppointmentProposal.Type.RESCHEDULE_CONFIRMED,
            options=[
                AppointmentProposalOptionInput(
                    start_at=matching_slot.start_at,
                    is_near_fit=matching_slot.is_near_fit,
                )
            ],
        )
        return ClientRescheduleResult(
            appointment=appointment,
            proposal=proposal,
            previous_start_at=previous_start_at,
            requires_master_confirmation=True,
            is_near_fit=matching_slot.is_near_fit,
        )

    moved = _auto_confirm_client_reschedule(
        partner=partner,
        client_profile=client_profile,
        appointment=appointment,
        start_at=start_at,
        now=current_time,
    )
    return ClientRescheduleResult(
        appointment=moved,
        proposal=None,
        previous_start_at=previous_start_at,
        requires_master_confirmation=False,
        is_near_fit=False,
    )


def cancel_client_requested_booking(
    *,
    client_profile: ClientProfile,
    appointment: Appointment,
    reason: str = "",
) -> Appointment:
    """Cancel a requested appointment from the client's personal area."""

    return cancel_requested_appointment_by_client(
        client_profile=client_profile,
        appointment=appointment,
        reason=reason,
    )


def client_appointment_history_page(
    *,
    client_profile: ClientProfile,
    page: int = 1,
    per_page: int = CLIENT_HISTORY_PAGE_SIZE,
) -> Page[Appointment]:
    """Return newest-to-oldest appointment history in Telegram-safe pages."""

    appointments = tuple(
        appointments_for_client(client_profile)
        .prefetch_related("service_items")
        .order_by("-start_at", "-created_at")
    )
    return paginate_telegram_entities(appointments, page=page, per_page=per_page)


def client_future_appointments_page(
    *,
    client_profile: ClientProfile,
    page: int = 1,
    per_page: int = CLIENT_HISTORY_PAGE_SIZE,
    now: datetime | None = None,
) -> Page[Appointment]:
    """Return future requested/confirmed appointments for the client profile."""

    current_time = now or timezone.now()
    appointments = tuple(
        appointments_for_client(client_profile)
        .filter(
            status__in=[Appointment.Status.REQUESTED, Appointment.Status.CONFIRMED],
            start_at__gte=current_time,
        )
        .prefetch_related("service_items")
        .order_by("start_at", "created_at")
    )
    return paginate_telegram_entities(appointments, page=page, per_page=per_page)


def client_profile_overview(
    *,
    client_profile: ClientProfile,
    future_page: int = 1,
    now: datetime | None = None,
) -> ClientProfileOverview:
    """Return basic personal-area profile data for the client."""

    visit_count = (
        appointments_for_client(client_profile).filter(status=Appointment.Status.COMPLETED).count()
    )
    return ClientProfileOverview(
        client_profile=client_profile,
        visit_count=visit_count,
        future_appointments=client_future_appointments_page(
            client_profile=client_profile,
            page=future_page,
            now=now,
        ),
    )


def _available_booking_date_options(
    *,
    partner: Partner,
    selected_service_ids: Iterable[UUID | str],
    start_date: date,
    days: int,
    booking_resource: BookingResource | None,
    now: datetime | None,
) -> tuple[BookingDateOption, ...]:
    """Compute a date range while loading schedule and busy data only once."""

    booking_resource = booking_resource or get_or_create_default_booking_resource(partner=partner)
    summary = build_booking_summary(partner=partner, selected_service_ids=selected_service_ids)
    return _available_date_options_for_duration(
        partner=partner,
        booking_resource=booking_resource,
        duration_minutes=summary.duration_minutes,
        start_date=start_date,
        days=days,
        now=now,
    )


def _available_date_options_for_duration(
    *,
    partner: Partner,
    booking_resource: BookingResource,
    duration_minutes: int,
    start_date: date,
    days: int,
    now: datetime | None,
    exclude_appointment: Appointment | None = None,
    excluded_start_ats: frozenset[datetime] = frozenset(),
) -> tuple[BookingDateOption, ...]:
    """Compute a date range once for catalog and snapshotted appointment durations."""

    current_time = now or timezone.now()
    schedule_settings = _schedule_settings_for_booking(partner)
    range_start, _first_day_end = _local_date_range(
        partner=partner,
        local_date=start_date,
    )
    _last_day_start, range_end = _local_date_range(
        partner=partner,
        local_date=start_date + timedelta(days=days - 1),
    )
    schedule_windows = tuple(
        availability_windows_for_resource(
            partner=partner,
            booking_resource=booking_resource,
            start_at=range_start,
            end_at=range_end,
        )
    )
    if not schedule_windows:
        return ()

    duration_and_tolerance = timedelta(
        minutes=duration_minutes + schedule_settings.near_fit_tolerance_minutes
    )
    blocked_intervals = tuple(
        TimeInterval(block.start_at, block.end_at)
        for block in blocked_times_for_resource(
            partner=partner,
            booking_resource=booking_resource,
            start_at=range_start,
            end_at=range_end + duration_and_tolerance,
        )
    )
    busy_intervals = confirmed_busy_intervals_for_resource(
        booking_resource=booking_resource,
        start_at=range_start,
        end_at=range_end + duration_and_tolerance,
        exclude_appointment=exclude_appointment,
    )
    options: list[BookingDateOption] = []
    for offset in range(days):
        local_date = start_date + timedelta(days=offset)
        day_start, day_end = _local_date_range(partner=partner, local_date=local_date)
        slots = generate_available_slots(
            partner=partner,
            booking_resource=booking_resource,
            duration_minutes=duration_minutes,
            range_start=day_start,
            range_end=day_end,
            schedule_settings=schedule_settings,
            busy_intervals=busy_intervals,
            schedule_windows=schedule_windows,
            blocked_intervals=blocked_intervals,
            now=current_time,
        )
        slots = tuple(slot for slot in slots if slot.start_at not in excluded_start_ats)
        if slots:
            options.append(
                BookingDateOption(
                    local_date=local_date,
                    slot_count=len(slots),
                    first_slot_at=slots[0].start_at,
                    has_near_fit=any(slot.is_near_fit for slot in slots),
                )
            )
    return tuple(options)


def _slot_options_for_local_date(
    *,
    partner: Partner,
    booking_resource: BookingResource,
    duration_minutes: int,
    local_date: date,
    now: datetime | None,
    exclude_appointment: Appointment | None = None,
) -> tuple[BookingSlotOption, ...]:
    schedule_settings = _schedule_settings_for_booking(partner)
    range_start, range_end = _local_date_range(partner=partner, local_date=local_date)
    busy_intervals = confirmed_busy_intervals_for_resource(
        booking_resource=booking_resource,
        start_at=range_start,
        end_at=range_end
        + timedelta(minutes=duration_minutes + schedule_settings.near_fit_tolerance_minutes),
        exclude_appointment=exclude_appointment,
    )
    slots = generate_available_slots(
        partner=partner,
        booking_resource=booking_resource,
        duration_minutes=duration_minutes,
        range_start=range_start,
        range_end=range_end,
        schedule_settings=schedule_settings,
        busy_intervals=busy_intervals,
        now=now,
    )
    return tuple(_slot_option(slot) for slot in slots)


def _find_matching_slot(
    *,
    partner: Partner,
    booking_resource: BookingResource,
    summary: BookingSummary,
    local_date: date,
    start_at: datetime,
    now: datetime | None,
) -> BookingSlotOption:
    slots = _slot_options_for_local_date(
        partner=partner,
        booking_resource=booking_resource,
        duration_minutes=summary.duration_minutes,
        local_date=local_date,
        now=now,
    )
    for slot in slots:
        if slot.start_at == start_at:
            return slot
    raise ValidationError({"start_at": "Selected slot is no longer available."})


def _reschedule_slot_options_for_local_date(
    *,
    appointment: Appointment,
    duration_minutes: int,
    local_date: date,
    now: datetime,
) -> tuple[BookingSlotOption, ...]:
    slots = _slot_options_for_local_date(
        partner=appointment.partner,
        booking_resource=appointment.booking_resource,
        duration_minutes=duration_minutes,
        local_date=local_date,
        now=now,
        exclude_appointment=appointment,
    )
    return tuple(slot for slot in slots if slot.start_at != appointment.start_at)


def _requested_alternative_slot_options_for_local_date(
    *,
    appointment: Appointment,
    local_date: date,
    now: datetime,
) -> tuple[BookingSlotOption, ...]:
    slots = _slot_options_for_local_date(
        partner=appointment.partner,
        booking_resource=appointment.booking_resource,
        duration_minutes=_appointment_duration_minutes(appointment),
        local_date=local_date,
        now=now,
    )
    return tuple(slot for slot in slots if slot.start_at != appointment.start_at)


def _find_matching_requested_alternative_slot(
    *,
    appointment: Appointment,
    start_at: datetime,
    now: datetime,
) -> BookingSlotOption:
    local_date = to_partner_time(start_at, appointment.partner.timezone).date()
    slots = _requested_alternative_slot_options_for_local_date(
        appointment=appointment,
        local_date=local_date,
        now=now,
    )
    for slot in slots:
        if slot.start_at == start_at:
            return slot
    raise ValidationError({"start_at": "Selected alternative is no longer available."})


def _find_matching_reschedule_slot(
    *,
    appointment: Appointment,
    start_at: datetime,
    now: datetime,
) -> BookingSlotOption:
    local_date = to_partner_time(start_at, appointment.partner.timezone).date()
    slots = _reschedule_slot_options_for_local_date(
        appointment=appointment,
        duration_minutes=_appointment_duration_minutes(appointment),
        local_date=local_date,
        now=now,
    )
    for slot in slots:
        if slot.start_at == start_at:
            return slot
    raise ValidationError({"start_at": "Selected slot is no longer available."})


def _auto_confirm_client_reschedule(
    *,
    partner: Partner,
    client_profile: ClientProfile,
    appointment: Appointment,
    start_at: datetime,
    now: datetime,
) -> Appointment:
    with transaction.atomic():
        appointment = (
            Appointment.objects.select_for_update()
            .select_related("partner", "booking_resource", "client_profile")
            .prefetch_related("service_items")
            .get(id=appointment.id)
        )
        _validate_client_reschedule(
            partner=partner,
            client_profile=client_profile,
            appointment=appointment,
            now=now,
        )
        acquire_booking_resource_advisory_lock_if_supported(appointment.booking_resource)
        matching_slot = _find_matching_reschedule_slot(
            appointment=appointment,
            start_at=start_at,
            now=now,
        )
        schedule_settings = _schedule_settings_for_booking(partner)
        if (
            matching_slot.is_near_fit
            or schedule_settings.confirmation_mode != PartnerScheduleSettings.ConfirmationMode.AUTO
        ):
            raise ValidationError({"start_at": "Booking settings changed. Choose the time again."})

        previous_start_at = appointment.start_at
        AppointmentProposal.objects.filter(
            appointment=appointment,
            status=AppointmentProposal.Status.PENDING,
        ).update(status=AppointmentProposal.Status.SUPERSEDED, responded_at=now)
        appointment.start_at = matching_slot.start_at
        appointment.end_at = matching_slot.end_at
        appointment.confirmation_mode_snapshot = schedule_settings.confirmation_mode
        appointment.is_near_fit = False
        appointment.save(
            update_fields=[
                "start_at",
                "end_at",
                "confirmation_mode_snapshot",
                "is_near_fit",
                "updated_at",
            ]
        )
        record_audit_event(
            partner=partner,
            actor=None,
            event_type="appointment.client_rescheduled",
            target=appointment,
            message="Client rescheduled a confirmed appointment.",
            payload={
                "previous_start_at": previous_start_at.isoformat(),
                "new_start_at": appointment.start_at.isoformat(),
            },
        )
        refresh_confirmed_notifications_after_commit(appointment.id)
        transaction.on_commit(
            lambda: _schedule_master_client_reschedule_notification(appointment.id)
        )
    return appointment


def _schedule_master_client_reschedule_notification(appointment_id) -> None:
    from apps.notifications.services import schedule_master_client_reschedule_notification

    appointment = (
        Appointment.objects.select_related("partner", "client_profile")
        .prefetch_related("service_items")
        .get(id=appointment_id)
    )
    schedule_master_client_reschedule_notification(appointment=appointment)


def _appointment_duration_minutes(appointment: Appointment) -> int:
    duration_minutes = sum(item.duration_minutes for item in appointment.service_items.all())
    if duration_minutes <= 0:
        raise ValidationError({"appointment": "Appointment has no service duration."})
    return duration_minutes


def _validate_client_reschedule(
    *,
    partner: Partner,
    client_profile: ClientProfile,
    appointment: Appointment,
    now: datetime,
) -> None:
    if client_profile.partner_id != partner.id:
        raise ValidationError({"client_profile": "Client profile must belong to this partner."})
    if appointment.partner_id != partner.id or appointment.client_profile_id != client_profile.id:
        raise ValidationError({"appointment": "Appointment is not available to this client."})
    if appointment.status != Appointment.Status.CONFIRMED:
        raise ValidationError({"appointment": "Only a confirmed appointment can be moved."})
    if appointment.start_at <= now:
        raise ValidationError({"appointment": "Past appointments cannot be moved."})


def _validate_requested_alternative_access(
    *,
    actor: User,
    partner: Partner,
    appointment: Appointment,
) -> None:
    ensure_partner_access(actor, partner)
    if appointment.partner_id != partner.id:
        raise ValidationError({"appointment": "Appointment is not available to this partner."})
    if appointment.status != Appointment.Status.REQUESTED:
        raise ValidationError({"appointment": "Appointment is no longer requested."})


def _unique_aware_datetimes(values: Iterable[datetime]) -> tuple[datetime, ...]:
    unique_values: list[datetime] = []
    seen: set[datetime] = set()
    for value in values:
        _validate_aware_datetime(value, "start_at")
        normalized = value.astimezone(datetime_timezone.utc)
        if normalized not in seen:
            seen.add(normalized)
            unique_values.append(normalized)
    return tuple(unique_values)


def _active_services_for_selection(
    *,
    partner: Partner,
    selected_service_ids: Iterable[UUID | str],
) -> tuple[Service, ...]:
    service_ids = _unique_service_ids(selected_service_ids)
    if not service_ids:
        raise ValidationError({"services": "Select at least one service."})

    services = tuple(
        active_services_for_booking(partner)
        .filter(id__in=service_ids)
        .select_related("category")
        .order_by("category__sort_order", "category__name", "sort_order", "name")
    )
    found_ids = {service.id for service in services}
    if found_ids != set(service_ids):
        raise ValidationError({"services": "One or more selected services are unavailable."})
    return services


def _unique_service_ids(selected_service_ids: Iterable[UUID | str]) -> tuple[UUID, ...]:
    ids: list[UUID] = []
    seen: set[UUID] = set()
    for raw_id in selected_service_ids:
        try:
            service_id = raw_id if isinstance(raw_id, UUID) else UUID(str(raw_id))
        except (TypeError, ValueError) as exc:
            raise ValidationError({"services": "Invalid service identifier."}) from exc
        if service_id not in seen:
            seen.add(service_id)
            ids.append(service_id)
    return tuple(ids)


def _service_line(service: Service) -> BookingServiceLine:
    return BookingServiceLine(
        service_id=service.id,
        category_id=service.category_id,
        category_name=service.category.name,
        name=service.name,
        description=service.description,
        price_amount=service.price_amount,
        currency=service.currency,
        duration_minutes=service.duration_minutes,
    )


def _slot_option(slot: GeneratedSlot) -> BookingSlotOption:
    return BookingSlotOption(
        start_at=slot.start_at,
        end_at=slot.end_at,
        is_near_fit=slot.is_near_fit,
    )


def _local_date_range(*, partner: Partner, local_date: date) -> tuple[datetime, datetime]:
    partner_zone = get_zone(partner.timezone)
    local_start = datetime.combine(local_date, time.min, tzinfo=partner_zone)
    local_end = local_start + timedelta(days=1)
    return (
        local_start.astimezone(datetime_timezone.utc),
        local_end.astimezone(datetime_timezone.utc),
    )


def _schedule_settings_for_booking(partner: Partner) -> PartnerScheduleSettings:
    schedule_settings, _created = PartnerScheduleSettings.objects.get_or_create(partner=partner)
    if schedule_settings.confirmation_mode not in PartnerScheduleSettings.ConfirmationMode.values:
        raise ValidationError({"confirmation_mode": "Booking confirmation mode is not configured."})
    return schedule_settings


def _validate_category_available_for_booking(
    *,
    partner: Partner,
    category: ServiceCategory,
) -> None:
    if category.partner_id != partner.id:
        raise ValidationError({"category": "Category must belong to this partner."})
    if not category.is_active:
        raise ValidationError({"category": "Category is not available for booking."})


def _validate_aware_datetime(value: datetime, field_name: str) -> None:
    if timezone.is_naive(value):
        raise ValidationError({field_name: "Datetime must be timezone-aware."})


def _single_currency(currencies: Iterable[str]) -> str:
    unique_currencies = {currency for currency in currencies}
    if not unique_currencies:
        return DEFAULT_CURRENCY
    if len(unique_currencies) != 1:
        raise ValidationError({"currency": "Selected services must use one currency."})
    return unique_currencies.pop()
