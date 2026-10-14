from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from datetime import timezone as datetime_timezone
from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.appointments.client_booking import (
    BookingSummary,
    available_booking_dates,
    available_booking_slots,
    booking_category_page,
    booking_services_page,
    build_booking_summary,
    confirm_client_booking,
)
from apps.catalog.models import ServiceCategory
from apps.clients.models import ClientProfile
from apps.partners.models import Partner
from apps.schedule.models import PartnerScheduleSettings
from core.money import Money
from core.timezones import to_partner_time

from .services import render_content_template

BOOKING_CALLBACK_PREFIX = "bk"
BOOKING_SEARCH_DAYS = 30


@dataclass(frozen=True, slots=True)
class BotBookingReply:
    """Text and inline keyboard ready for a bot booking adapter response."""

    text: str
    reply_markup: InlineKeyboardMarkup | None = None


def build_booking_categories_reply(
    *,
    partner: Partner,
    language: str,
    page: int = 1,
) -> BotBookingReply:
    """Render the first booking screen with active service categories."""

    category_page = booking_category_page(partner=partner, page=page)
    if not category_page.items:
        return _reply(partner=partner, key="booking.no_categories", language=language)

    rows = [
        [
            InlineKeyboardButton(
                text=category.name,
                callback_data=_callback("svc", category.id.hex, 1),
            )
        ]
        for category in category_page.items
    ]
    rows.extend(_pagination_rows("cat", category_page.page, category_page))
    return _reply(
        partner=partner,
        key="booking.category_select",
        language=language,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def build_booking_services_reply(
    *,
    partner: Partner,
    category_id: UUID,
    language: str,
    page: int = 1,
) -> BotBookingReply:
    """Render services for one trusted partner category."""

    category = _category_for_partner(partner=partner, category_id=category_id)
    services_page = booking_services_page(
        partner=partner,
        category=category,
        page=page,
    )
    if not services_page.items:
        return _reply(partner=partner, key="booking.no_services", language=language)

    rows = [
        [
            InlineKeyboardButton(
                text=_service_button_label(service, language),
                callback_data=_callback("sum", service.id.hex),
            )
        ]
        for service in services_page.items
    ]
    rows.extend(_pagination_rows("svc", services_page.page, services_page, category.id.hex))
    rows.append([_back_button(_callback("cat", 1), language)])
    return _reply(
        partner=partner,
        key="booking.service_select",
        language=language,
        context={"category_name": category.name},
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def build_booking_summary_reply(
    *,
    partner: Partner,
    service_id: UUID,
    language: str,
) -> BotBookingReply:
    """Render selected service summary and let the client choose a date."""

    summary = build_booking_summary(partner=partner, selected_service_ids=[service_id])
    rows = [
        [
            InlineKeyboardButton(
                text=_label("choose_date", language),
                callback_data=_callback("dates", service_id.hex, 1),
            )
        ],
        [_back_button(_callback("cat", 1), language)],
    ]
    return _reply(
        partner=partner,
        key="booking.summary",
        language=language,
        context=_summary_context(summary=summary, language=language),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def build_booking_dates_reply(
    *,
    partner: Partner,
    service_id: UUID,
    language: str,
    page: int = 1,
    now: datetime | None = None,
) -> BotBookingReply:
    """Render local dates with at least one slot for the selected service."""

    current_time = now or timezone.now()
    settings = _schedule_settings(partner)
    start_date = to_partner_time(current_time, partner.timezone).date()
    days = min(settings.booking_horizon_days, BOOKING_SEARCH_DAYS)
    dates_page = available_booking_dates(
        partner=partner,
        selected_service_ids=[service_id],
        start_date=start_date,
        days=days,
        page=page,
        now=current_time,
    )
    if not dates_page.items:
        return _reply(partner=partner, key="booking.no_dates", language=language)

    rows = [
        [
            InlineKeyboardButton(
                text=_date_button_label(option.local_date, option.slot_count, language),
                callback_data=_callback(
                    "slots",
                    service_id.hex,
                    option.local_date.strftime("%Y%m%d"),
                    1,
                ),
            )
        ]
        for option in dates_page.items
    ]
    rows.extend(_pagination_rows("dates", dates_page.page, dates_page, service_id.hex))
    rows.append([_back_button(_callback("sum", service_id.hex), language)])
    return _reply(
        partner=partner,
        key="booking.date_select",
        language=language,
        context={"booking_horizon_days": str(settings.booking_horizon_days)},
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def build_booking_slots_reply(
    *,
    partner: Partner,
    service_id: UUID,
    local_date: date,
    language: str,
    page: int = 1,
    now: datetime | None = None,
) -> BotBookingReply:
    """Render slots for one selected local date."""

    summary = build_booking_summary(partner=partner, selected_service_ids=[service_id])
    slots_page = available_booking_slots(
        partner=partner,
        selected_service_ids=[service_id],
        local_date=local_date,
        page=page,
        now=now,
    )
    if not slots_page.items:
        return _reply(partner=partner, key="booking.no_slots", language=language)

    rows = [
        [
            InlineKeyboardButton(
                text=_slot_button_label(
                    slot.start_at,
                    partner.timezone,
                    slot.is_near_fit,
                    language,
                ),
                callback_data=_callback("final", service_id.hex, _timestamp_token(slot.start_at)),
            )
        ]
        for slot in slots_page.items
    ]
    rows.extend(
        _pagination_rows(
            "slots",
            slots_page.page,
            slots_page,
            service_id.hex,
            local_date.strftime("%Y%m%d"),
        )
    )
    rows.append([_back_button(_callback("dates", service_id.hex, 1), language)])
    return _reply(
        partner=partner,
        key="booking.time_select",
        language=language,
        context={
            "date": local_date.isoformat(),
            "services": _services_label(summary),
            "duration": _duration_label(summary.duration_minutes, language),
        },
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def build_booking_final_confirm_reply(
    *,
    partner: Partner,
    service_id: UUID,
    start_at: datetime,
    language: str,
    now: datetime | None = None,
) -> BotBookingReply:
    """Render final confirmation for a selected slot after fresh slot validation."""

    summary = build_booking_summary(partner=partner, selected_service_ids=[service_id])
    selected_slot = _find_slot_for_final_confirm(
        partner=partner,
        service_id=service_id,
        local_date=to_partner_time(start_at, partner.timezone).date(),
        start_at=start_at,
        now=now,
    )
    if selected_slot is None:
        return _reply(partner=partner, key="booking.slot_taken", language=language)

    text = _render(
        partner=partner,
        key="booking.final_confirm",
        language=language,
        context=_summary_context(summary=summary, language=language)
        | _datetime_context(partner=partner, start_at=start_at),
    )
    if selected_slot.is_near_fit:
        settings = _schedule_settings(partner)
        near_fit_notice = _render(
            partner=partner,
            key="booking.near_fit_notice",
            language=language,
            context={"tolerance_minutes": str(settings.near_fit_tolerance_minutes)},
        )
        text = f"{text}\n\n{near_fit_notice}"

    rows = [
        [
            InlineKeyboardButton(
                text=_label("confirm", language),
                callback_data=_callback("create", service_id.hex, _timestamp_token(start_at)),
            )
        ],
        [
            _back_button(
                _callback(
                    "slots",
                    service_id.hex,
                    to_partner_time(start_at, partner.timezone).strftime("%Y%m%d"),
                    1,
                ),
                language,
            )
        ],
    ]
    return BotBookingReply(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


def confirm_booking_reply(
    *,
    partner: Partner,
    client_profile: ClientProfile,
    service_id: UUID,
    start_at: datetime,
    language: str,
    now: datetime | None = None,
) -> BotBookingReply:
    """Create a booking from Telegram final confirmation and render result text."""

    result = confirm_client_booking(
        partner=partner,
        client_profile=client_profile,
        selected_service_ids=[service_id],
        start_at=start_at,
        now=now,
    )
    key = (
        "booking.requested_created"
        if result.requires_master_confirmation
        else "booking.confirmed_created"
    )
    return _reply(
        partner=partner,
        key=key,
        language=language,
        context={
            "partner_name": partner.name,
            "services": _appointment_services_label(result.appointment),
        }
        | _datetime_context(partner=partner, start_at=result.appointment.start_at),
    )


def parse_uuid_token(value: str) -> UUID:
    """Parse compact UUID callback token."""

    try:
        return UUID(hex=value)
    except ValueError as exc:
        raise ValidationError({"callback": "Invalid callback identifier."}) from exc


def parse_date_token(value: str) -> date:
    """Parse compact local date callback token."""

    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as exc:
        raise ValidationError({"callback": "Invalid callback date."}) from exc


def parse_timestamp_token(value: str) -> datetime:
    """Parse UTC timestamp callback token."""

    try:
        return datetime.fromtimestamp(int(value), tz=datetime_timezone.utc)
    except (TypeError, ValueError, OSError) as exc:
        raise ValidationError({"callback": "Invalid callback time."}) from exc


def parse_page_token(value: str) -> int:
    """Parse a positive callback page number."""

    try:
        return max(1, int(value))
    except (TypeError, ValueError) as exc:
        raise ValidationError({"callback": "Invalid callback page."}) from exc


def _reply(
    *,
    partner: Partner,
    key: str,
    language: str,
    context: dict | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> BotBookingReply:
    return BotBookingReply(
        text=_render(partner=partner, key=key, language=language, context=context),
        reply_markup=reply_markup,
    )


def _render(
    *,
    partner: Partner,
    key: str,
    language: str,
    context: dict | None = None,
) -> str:
    return render_content_template(
        partner=partner,
        key=key,
        language=language,
        context=context or {},
    ).body


def _callback(*parts: object) -> str:
    return ":".join([BOOKING_CALLBACK_PREFIX, *(str(part) for part in parts)])


def _pagination_rows(action: str, current_page: int, page, *extra_parts: object):
    if not page.has_previous and not page.has_next:
        return []

    row: list[InlineKeyboardButton] = []
    if page.has_previous:
        row.append(
            InlineKeyboardButton(
                text="<",
                callback_data=_callback(action, *extra_parts, current_page - 1),
            )
        )
    row.append(
        InlineKeyboardButton(
            text=page.indicator,
            callback_data=_callback("noop"),
        )
    )
    if page.has_next:
        row.append(
            InlineKeyboardButton(
                text=">",
                callback_data=_callback(action, *extra_parts, current_page + 1),
            )
        )
    return [row]


def _back_button(callback_data: str, language: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=_label("back", language), callback_data=callback_data)


def _category_for_partner(*, partner: Partner, category_id: UUID) -> ServiceCategory:
    try:
        return ServiceCategory.objects.get(partner=partner, id=category_id)
    except ServiceCategory.DoesNotExist as exc:
        raise ValidationError({"category": "Category is not available."}) from exc


def _schedule_settings(partner: Partner) -> PartnerScheduleSettings:
    settings, _created = PartnerScheduleSettings.objects.get_or_create(partner=partner)
    return settings


def _find_slot_for_final_confirm(
    *,
    partner: Partner,
    service_id: UUID,
    local_date: date,
    start_at: datetime,
    now: datetime | None,
):
    page_number = 1
    while True:
        slot_page = available_booking_slots(
            partner=partner,
            selected_service_ids=[service_id],
            local_date=local_date,
            page=page_number,
            now=now,
        )
        for slot in slot_page.items:
            if slot.start_at == start_at:
                return slot
        if not slot_page.has_next:
            return None
        page_number += 1


def _summary_context(*, summary: BookingSummary, language: str) -> dict[str, str]:
    return {
        "services": _services_label(summary),
        "duration": _duration_label(summary.duration_minutes, language),
        "service_total": _money_label(summary.service_total_amount, summary.currency),
        "currency": summary.currency,
    }


def _datetime_context(*, partner: Partner, start_at: datetime) -> dict[str, str]:
    partner_time = to_partner_time(start_at, partner.timezone)
    return {
        "date": partner_time.date().isoformat(),
        "time": partner_time.strftime("%H:%M"),
    }


def _services_label(summary: BookingSummary) -> str:
    return "\n".join(line.name for line in summary.service_lines)


def _appointment_services_label(appointment) -> str:
    return ", ".join(item.service_name_snapshot for item in appointment.service_items.all())


def _service_button_label(service, language: str) -> str:
    return (
        f"{service.name} · "
        f"{_duration_label(service.duration_minutes, language)} · "
        f"{_money_label(service.price_amount, service.currency)} {service.currency}"
    )


def _date_button_label(local_date: date, slot_count: int, language: str) -> str:
    return f"{local_date.isoformat()} · {slot_count} {_label('slots', language)}"


def _slot_button_label(
    start_at: datetime,
    timezone_name: str,
    is_near_fit: bool,
    language: str,
) -> str:
    partner_time = to_partner_time(start_at, timezone_name)
    label = partner_time.strftime("%H:%M")
    if is_near_fit:
        label = f"{label} · {_label('needs_confirmation', language)}"
    return label


def _duration_label(minutes: int, language: str) -> str:
    unit = _label("minutes", language)
    return f"{minutes} {unit}"


def _money_label(amount: int, currency: str) -> str:
    return str(Money(amount=amount, currency=currency).to_major_decimal())


def _timestamp_token(value: datetime) -> int:
    return int(value.astimezone(datetime_timezone.utc).timestamp())


def _label(key: str, language: str) -> str:
    labels = {
        "back": {"uk": "Назад", "ru": "Назад", "en": "Back"},
        "choose_date": {"uk": "Обрати дату", "ru": "Выбрать дату", "en": "Choose date"},
        "confirm": {"uk": "Підтвердити", "ru": "Подтвердить", "en": "Confirm"},
        "slots": {"uk": "слотів", "ru": "слотов", "en": "slots"},
        "minutes": {"uk": "хв", "ru": "мин", "en": "min"},
        "needs_confirmation": {
            "uk": "потрібне підтвердження",
            "ru": "нужно подтверждение",
            "en": "needs confirmation",
        },
    }
    translations = labels[key]
    return translations.get(language, translations["uk"])
