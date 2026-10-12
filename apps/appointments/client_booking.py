from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from datetime import timezone as datetime_timezone
from typing import TYPE_CHECKING
from uuid import UUID

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.appointments.models import Appointment
from apps.appointments.selectors import (
    appointments_for_client,
    confirmed_busy_intervals_for_resource,
)
from apps.appointments.services import (
    AppointmentServiceItemInput,
    cancel_requested_appointment_by_client,
    create_confirmed_appointment,
    create_requested_appointment,
)
from apps.bot.pagination import (
    DEFAULT_TELEGRAM_ENTITY_PAGE_SIZE,
    Page,
    paginate_telegram_entities,
)
from apps.catalog.models import DEFAULT_CURRENCY, Service, ServiceCategory
from apps.catalog.selectors import active_categories_for_booking, active_services_for_booking
from apps.schedule.models import PartnerScheduleSettings
from apps.schedule.services import (
    GeneratedSlot,
    generate_available_slots,
    get_or_create_default_booking_resource,
)
from core.timezones import get_zone, to_partner_time

if TYPE_CHECKING:
    from apps.clients.models import ClientProfile
    from apps.partners.models import Partner
    from apps.schedule.models import BookingResource


BOOKING_DATES_PAGE_SIZE = 7
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

    booking_resource = booking_resource or get_or_create_default_booking_resource(partner=partner)
    summary = build_booking_summary(partner=partner, selected_service_ids=selected_service_ids)
    options: list[BookingDateOption] = []

    for offset in range(days):
        local_date = start_date + timedelta(days=offset)
        slots = _slot_options_for_local_date(
            partner=partner,
            booking_resource=booking_resource,
            summary=summary,
            local_date=local_date,
            now=now,
        )
        if slots:
            options.append(
                BookingDateOption(
                    local_date=local_date,
                    slot_count=len(slots),
                    first_slot_at=slots[0].start_at,
                    has_near_fit=any(slot.is_near_fit for slot in slots),
                )
            )

    return paginate_telegram_entities(tuple(options), page=page, per_page=per_page)


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
        summary=summary,
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

    visit_count = appointments_for_client(client_profile).filter(
        status=Appointment.Status.COMPLETED
    ).count()
    return ClientProfileOverview(
        client_profile=client_profile,
        visit_count=visit_count,
        future_appointments=client_future_appointments_page(
            client_profile=client_profile,
            page=future_page,
            now=now,
        ),
    )


def _slot_options_for_local_date(
    *,
    partner: Partner,
    booking_resource: BookingResource,
    summary: BookingSummary,
    local_date: date,
    now: datetime | None,
) -> tuple[BookingSlotOption, ...]:
    schedule_settings = _schedule_settings_for_booking(partner)
    range_start, range_end = _local_date_range(partner=partner, local_date=local_date)
    busy_intervals = confirmed_busy_intervals_for_resource(
        booking_resource=booking_resource,
        start_at=range_start,
        end_at=range_end
        + timedelta(
            minutes=summary.duration_minutes + schedule_settings.near_fit_tolerance_minutes
        ),
    )
    slots = generate_available_slots(
        partner=partner,
        booking_resource=booking_resource,
        duration_minutes=summary.duration_minutes,
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
        summary=summary,
        local_date=local_date,
        now=now,
    )
    for slot in slots:
        if slot.start_at == start_at:
            return slot
    raise ValidationError({"start_at": "Selected slot is no longer available."})


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
        raise ValidationError(
            {"confirmation_mode": "Booking confirmation mode is not configured."}
        )
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
