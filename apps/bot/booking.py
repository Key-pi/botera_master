from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import timezone as datetime_timezone
from hashlib import sha256
from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.appointments.client_booking import (
    BookingServiceLine,
    BookingSummary,
    available_booking_dates,
    available_booking_months,
    available_booking_slots,
    booking_category_page,
    booking_services_page,
    build_booking_summary,
    confirm_client_booking,
)
from apps.bot.pagination import paginate_telegram_entities
from apps.catalog.models import Service, ServiceCategory
from apps.catalog.selectors import active_services_for_booking
from apps.clients.models import ClientProfile
from apps.partners.models import Partner
from apps.schedule.models import PartnerScheduleSettings
from core.money import Money
from core.timezones import to_partner_time

from .services import render_content_template

BOOKING_CALLBACK_PREFIX = "bk"
BOOKING_SUMMARY_PAGE_SIZE = 5
MAX_BOOKING_COMMENT_LENGTH = 1000
NEAREST_DATE_SOURCE = "n"


@dataclass(frozen=True, slots=True)
class BotBookingReply:
    """Text and inline keyboard ready for a bot booking adapter response."""

    text: str
    reply_markup: InlineKeyboardMarkup | None = None


@dataclass(frozen=True, slots=True)
class BookingSelectionChange:
    """Validated ephemeral selection update for the Telegram adapter."""

    selected_service_ids: tuple[UUID, ...]
    category_id: UUID


def build_booking_categories_reply(
    *,
    partner: Partner,
    language: str,
    page: int = 1,
    selected_service_ids: Iterable[UUID | str] = (),
) -> BotBookingReply:
    """Render the first booking screen with active service categories."""

    selected_ids = _coerce_service_ids(selected_service_ids)
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
    rows.extend(
        _selection_control_rows(
            selected_count=len(selected_ids),
            language=language,
            done_callback=_callback("sum", 1),
            clear_callback=_callback("clearcat", category_page.page),
        )
    )
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
    selected_service_ids: Iterable[UUID | str] = (),
) -> BotBookingReply:
    """Render services for one trusted partner category."""

    selected_ids = _coerce_service_ids(selected_service_ids)
    selected_id_set = set(selected_ids)
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
                text=_service_button_label(
                    service,
                    language,
                    selected=service.id in selected_id_set,
                ),
                callback_data=_callback("toggle", service.id.hex, services_page.page),
            )
        ]
        for service in services_page.items
    ]
    rows.extend(_pagination_rows("svc", services_page.page, services_page, category.id.hex))
    rows.extend(
        _selection_control_rows(
            selected_count=len(selected_ids),
            language=language,
            done_callback=_callback("sum", 1),
            clear_callback=_callback("clear", category.id.hex, services_page.page),
        )
    )
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
    selected_service_ids: Iterable[UUID | str],
    language: str,
    page: int = 1,
    selection_changed: bool = False,
) -> BotBookingReply:
    """Render a paginated multi-service summary before date selection."""

    summary = build_booking_summary(
        partner=partner,
        selected_service_ids=selected_service_ids,
    )
    service_page = paginate_telegram_entities(
        summary.service_lines,
        page=page,
        per_page=BOOKING_SUMMARY_PAGE_SIZE,
    )
    rows = [
        [
            InlineKeyboardButton(
                text=f"{_label('remove', language)}: {line.name}",
                callback_data=_callback("remove", line.service_id.hex, service_page.page),
            )
        ]
        for line in service_page.items
    ]
    rows.extend(_pagination_rows("sum", service_page.page, service_page))
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=_label("choose_date", language),
                    callback_data=_callback("dates", 1),
                )
            ],
            [
                InlineKeyboardButton(
                    text=_label("clear", language),
                    callback_data=_callback("clearcat", 1),
                )
            ],
            [_back_button(_callback("restart", 1), language)],
        ]
    )
    reply = _reply(
        partner=partner,
        key="booking.summary",
        language=language,
        context=_summary_context(
            summary=summary,
            language=language,
            visible_lines=service_page.items,
            page_indicator=(service_page.indicator if service_page.total_pages > 1 else ""),
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    if not selection_changed:
        return reply
    notice = _render(partner=partner, key="booking.selection_changed", language=language)
    return BotBookingReply(
        text=f"{notice}\n\n{reply.text}",
        reply_markup=reply.reply_markup,
    )


def build_booking_dates_reply(
    *,
    partner: Partner,
    selected_service_ids: Iterable[UUID | str],
    language: str,
    page: int = 1,
    now: datetime | None = None,
) -> BotBookingReply:
    """Render local dates with slots for the selected service set."""

    current_time = now or timezone.now()
    settings = _schedule_settings(partner)
    start_date = to_partner_time(current_time, partner.timezone).date()
    dates_page = available_booking_dates(
        partner=partner,
        selected_service_ids=selected_service_ids,
        start_date=start_date,
        days=settings.booking_horizon_days,
        page=page,
        now=current_time,
    )
    if not dates_page.items:
        return _reply(
            partner=partner,
            key="booking.no_dates",
            language=language,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=_no_dates_rows(language=language),
            ),
        )

    rows = [
        [
            InlineKeyboardButton(
                text=_date_button_label(option.local_date, option.slot_count, language),
                callback_data=_callback(
                    "slots",
                    option.local_date.strftime("%Y%m%d"),
                    1,
                    NEAREST_DATE_SOURCE,
                ),
            )
        ]
        for option in dates_page.items
    ]
    rows.extend(_pagination_rows("dates", dates_page.page, dates_page))
    rows.append(
        [
            InlineKeyboardButton(
                text=_label("choose_month", language),
                callback_data=_callback("months", 1),
            )
        ]
    )
    rows.append([_back_button(_callback("sum", 1), language)])
    return _reply(
        partner=partner,
        key="booking.date_select",
        language=language,
        context={"booking_horizon_days": str(settings.booking_horizon_days)},
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def build_booking_months_reply(
    *,
    partner: Partner,
    selected_service_ids: Iterable[UUID | str],
    language: str,
    page: int = 1,
    now: datetime | None = None,
) -> BotBookingReply:
    """Render months that contain available dates inside the booking horizon."""

    current_time = now or timezone.now()
    settings = _schedule_settings(partner)
    start_date = to_partner_time(current_time, partner.timezone).date()
    months_page = available_booking_months(
        partner=partner,
        selected_service_ids=selected_service_ids,
        start_date=start_date,
        days=settings.booking_horizon_days,
        page=page,
        now=current_time,
    )
    if not months_page.items:
        return _reply(
            partner=partner,
            key="booking.no_dates",
            language=language,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=_no_dates_rows(language=language),
            ),
        )

    rows = [
        [
            InlineKeyboardButton(
                text=_month_button_label(
                    option.month_start,
                    option.available_date_count,
                    language,
                ),
                callback_data=_callback("month", _month_token(option.month_start), 1),
            )
        ]
        for option in months_page.items
    ]
    rows.extend(_pagination_rows("months", months_page.page, months_page))
    rows.append([_back_button(_callback("dates", 1), language)])
    return _reply(
        partner=partner,
        key="booking.month_select",
        language=language,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def build_booking_month_dates_reply(
    *,
    partner: Partner,
    selected_service_ids: Iterable[UUID | str],
    month_start: date,
    language: str,
    page: int = 1,
    now: datetime | None = None,
) -> BotBookingReply:
    """Render available dates within one selected local calendar month."""

    current_time = now or timezone.now()
    settings = _schedule_settings(partner)
    horizon_start = to_partner_time(current_time, partner.timezone).date()
    horizon_end = horizon_start + timedelta(days=settings.booking_horizon_days)
    if month_start.day != 1:
        raise ValidationError({"month": "Month must start on its first day."})
    month_end = _next_month(month_start)
    search_start = max(horizon_start, month_start)
    search_end = min(horizon_end, month_end)
    if search_start >= search_end:
        raise ValidationError({"month": "Month is outside the booking horizon."})

    dates_page = available_booking_dates(
        partner=partner,
        selected_service_ids=selected_service_ids,
        start_date=search_start,
        days=(search_end - search_start).days,
        page=page,
        now=current_time,
    )
    month_token = _month_token(month_start)
    if not dates_page.items:
        return _reply(
            partner=partner,
            key="booking.no_dates",
            language=language,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [_back_button(_callback("months", 1), language)],
                    *_no_dates_rows(language=language),
                ]
            ),
        )

    rows = [
        [
            InlineKeyboardButton(
                text=_date_button_label(option.local_date, option.slot_count, language),
                callback_data=_callback(
                    "slots",
                    option.local_date.strftime("%Y%m%d"),
                    1,
                    month_token,
                ),
            )
        ]
        for option in dates_page.items
    ]
    rows.extend(_pagination_rows("month", dates_page.page, dates_page, month_token))
    rows.append([_back_button(_callback("months", 1), language)])
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
    selected_service_ids: Iterable[UUID | str],
    local_date: date,
    language: str,
    page: int = 1,
    date_source: str = NEAREST_DATE_SOURCE,
    now: datetime | None = None,
) -> BotBookingReply:
    """Render slots for one selected local date."""

    validate_date_source_token(date_source=date_source, local_date=local_date)
    summary = build_booking_summary(
        partner=partner,
        selected_service_ids=selected_service_ids,
    )
    slots_page = available_booking_slots(
        partner=partner,
        selected_service_ids=selected_service_ids,
        local_date=local_date,
        page=page,
        now=now,
    )
    if not slots_page.items:
        return _reply(
            partner=partner,
            key="booking.no_slots",
            language=language,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [_back_button(_date_list_callback(date_source), language)],
                    [
                        InlineKeyboardButton(
                            text=_label("change_services", language),
                            callback_data=_callback("restart", 1),
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text=_label("contact_master", language),
                            callback_data="pf:contact",
                        )
                    ],
                ]
            ),
        )

    rows = [
        [
            InlineKeyboardButton(
                text=_slot_button_label(
                    slot.start_at,
                    partner.timezone,
                    slot.is_near_fit,
                    language,
                ),
                callback_data=_callback(
                    "final",
                    _timestamp_token(slot.start_at),
                    date_source,
                ),
            )
        ]
        for slot in slots_page.items
    ]
    rows.extend(
        _pagination_rows(
            "slots",
            slots_page.page,
            slots_page,
            local_date.strftime("%Y%m%d"),
            date_source,
        )
    )
    rows.append([_back_button(_date_list_callback(date_source), language)])
    return _reply(
        partner=partner,
        key="booking.time_select",
        language=language,
        context={
            "date": local_date.isoformat(),
            "services": _services_label(summary.service_lines),
            "duration": _duration_label(summary.duration_minutes, language),
        },
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def build_booking_final_confirm_reply(
    *,
    partner: Partner,
    selected_service_ids: Iterable[UUID | str],
    start_at: datetime,
    language: str,
    date_source: str = NEAREST_DATE_SOURCE,
    client_comment: str = "",
    now: datetime | None = None,
) -> BotBookingReply:
    """Render final confirmation for a selected slot after fresh slot validation."""

    local_date = to_partner_time(start_at, partner.timezone).date()
    validate_date_source_token(date_source=date_source, local_date=local_date)
    summary = build_booking_summary(
        partner=partner,
        selected_service_ids=selected_service_ids,
    )
    selected_slot = _find_slot_for_final_confirm(
        partner=partner,
        selected_service_ids=selected_service_ids,
        local_date=local_date,
        start_at=start_at,
        now=now,
    )
    if selected_slot is None:
        return build_booking_slot_taken_reply(
            partner=partner,
            start_at=start_at,
            language=language,
            date_source=date_source,
        )

    cleaned_comment = normalize_booking_comment(client_comment)
    text = _render(
        partner=partner,
        key="booking.final_confirm",
        language=language,
        context=_summary_context(summary=summary, language=language)
        | _datetime_context(partner=partner, start_at=start_at)
        | {"comment": _booking_comment_line(cleaned_comment, language)},
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
                callback_data=_callback(
                    "create",
                    _timestamp_token(start_at),
                    date_source,
                ),
            )
        ],
        [
            InlineKeyboardButton(
                text=_label("change_comment" if cleaned_comment else "add_comment", language),
                callback_data=_callback(
                    "comment",
                    _timestamp_token(start_at),
                    date_source,
                ),
            )
        ],
    ]
    if cleaned_comment:
        rows.append(
            [
                InlineKeyboardButton(
                    text=_label("remove_comment", language),
                    callback_data=_callback(
                        "uncomment",
                        _timestamp_token(start_at),
                        date_source,
                    ),
                )
            ]
        )
    rows.append(
        [
            _back_button(
                _callback(
                    "slots",
                    local_date.strftime("%Y%m%d"),
                    1,
                    date_source,
                ),
                language,
            )
        ]
    )
    return BotBookingReply(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


def build_booking_comment_prompt_reply(
    *,
    partner: Partner,
    start_at: datetime,
    language: str,
    date_source: str,
    client_comment: str = "",
) -> BotBookingReply:
    """Ask for an optional booking comment while preserving the chosen slot."""

    local_date = to_partner_time(start_at, partner.timezone).date()
    validate_date_source_token(date_source=date_source, local_date=local_date)
    cleaned_comment = normalize_booking_comment(client_comment)
    rows: list[list[InlineKeyboardButton]] = []
    if cleaned_comment:
        rows.append(
            [
                InlineKeyboardButton(
                    text=_label("remove_comment", language),
                    callback_data=_callback(
                        "uncomment",
                        _timestamp_token(start_at),
                        date_source,
                    ),
                )
            ]
        )
    rows.append(
        [
            _back_button(
                _callback("final", _timestamp_token(start_at), date_source),
                language,
            )
        ]
    )
    return _reply(
        partner=partner,
        key="booking.comment_prompt",
        language=language,
        context={"max_length": str(MAX_BOOKING_COMMENT_LENGTH)},
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def build_booking_required_phone_reply(
    *,
    partner: Partner,
    start_at: datetime,
    language: str,
    date_source: str,
) -> BotBookingReply:
    """Ask for a missing partner-required phone before creating the booking."""

    local_date = to_partner_time(start_at, partner.timezone).date()
    validate_date_source_token(date_source=date_source, local_date=local_date)
    return _reply(
        partner=partner,
        key="error.required_field_missing",
        language=language,
        context={"missing_fields": _label("phone_number", language)},
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    _back_button(
                        _callback("final", _timestamp_token(start_at), date_source),
                        language,
                    )
                ]
            ]
        ),
    )


def build_booking_slot_taken_reply(
    *,
    partner: Partner,
    start_at: datetime,
    language: str,
    date_source: str,
) -> BotBookingReply:
    """Return a stale-slot error with navigation back to the originating date list."""

    local_date = to_partner_time(start_at, partner.timezone).date()
    validate_date_source_token(date_source=date_source, local_date=local_date)
    return _reply(
        partner=partner,
        key="booking.slot_taken",
        language=language,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    _back_button(
                        _callback(
                            "slots",
                            local_date.strftime("%Y%m%d"),
                            1,
                            date_source,
                        ),
                        language,
                    )
                ]
            ]
        ),
    )


def confirm_booking_reply(
    *,
    partner: Partner,
    client_profile: ClientProfile,
    selected_service_ids: Iterable[UUID | str],
    start_at: datetime,
    language: str,
    client_comment: str = "",
    now: datetime | None = None,
) -> BotBookingReply:
    """Create a booking from Telegram final confirmation and render result text."""

    result = confirm_client_booking(
        partner=partner,
        client_profile=client_profile,
        selected_service_ids=selected_service_ids,
        start_at=start_at,
        client_comment=normalize_booking_comment(client_comment),
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


def booking_requires_phone(*, partner: Partner, client_profile: ClientProfile) -> bool:
    """Return whether this client is missing the partner-required booking phone."""

    return (
        _schedule_settings(partner).require_phone_for_booking and not client_profile.phone.strip()
    )


def normalize_booking_comment(value: str) -> str:
    """Normalize one optional bot comment and keep final messages Telegram-safe."""

    if not isinstance(value, str):
        raise ValidationError({"client_comment": "Booking comment must be text."})
    cleaned = value.strip()
    if len(cleaned) > MAX_BOOKING_COMMENT_LENGTH:
        raise ValidationError(
            {
                "client_comment": (
                    f"Booking comment must be {MAX_BOOKING_COMMENT_LENGTH} characters or shorter."
                )
            }
        )
    return cleaned


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


def parse_month_token(value: str) -> date:
    """Parse a compact local calendar month callback token."""

    try:
        return datetime.strptime(value, "%Y%m").date().replace(day=1)
    except ValueError as exc:
        raise ValidationError({"callback": "Invalid callback month."}) from exc


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


def toggle_booking_service_selection(
    *,
    partner: Partner,
    selected_service_ids: Iterable[UUID | str],
    service_id: UUID,
) -> BookingSelectionChange:
    """Toggle one active partner service in an ephemeral booking selection."""

    service = _active_service_for_partner(partner=partner, service_id=service_id)
    selected_ids = list(_coerce_service_ids(selected_service_ids))
    if service.id in selected_ids:
        selected_ids.remove(service.id)
    else:
        selected_ids.append(service.id)
    return BookingSelectionChange(
        selected_service_ids=tuple(selected_ids),
        category_id=service.category_id,
    )


def remove_booking_service_selection(
    *,
    selected_service_ids: Iterable[UUID | str],
    service_id: UUID,
) -> tuple[UUID, ...]:
    """Remove one selected service without trusting a callback-only identifier."""

    selected_ids = list(_coerce_service_ids(selected_service_ids))
    if service_id not in selected_ids:
        raise ValidationError({"service": "Service is not part of this booking selection."})
    selected_ids.remove(service_id)
    return tuple(selected_ids)


def booking_selection_signature(
    *,
    partner: Partner,
    selected_service_ids: Iterable[UUID | str],
) -> str:
    """Fingerprint the client-visible service values reviewed in the summary."""

    summary = build_booking_summary(
        partner=partner,
        selected_service_ids=selected_service_ids,
    )
    values = sorted(
        (
            line.service_id.hex,
            line.name,
            line.description,
            str(line.price_amount),
            line.currency,
            str(line.duration_minutes),
        )
        for line in summary.service_lines
    )
    serialized = "\x1f".join("\x1e".join(value) for value in values)
    return sha256(serialized.encode("utf-8")).hexdigest()


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


def _selection_control_rows(
    *,
    selected_count: int,
    language: str,
    done_callback: str,
    clear_callback: str,
) -> list[list[InlineKeyboardButton]]:
    if selected_count <= 0:
        return []
    return [
        [
            InlineKeyboardButton(
                text=f"{_label('done', language)} ({selected_count})",
                callback_data=done_callback,
            )
        ],
        [
            InlineKeyboardButton(
                text=_label("clear", language),
                callback_data=clear_callback,
            )
        ],
    ]


def _no_dates_rows(*, language: str) -> list[list[InlineKeyboardButton]]:
    return [
        [
            InlineKeyboardButton(
                text=_label("change_services", language),
                callback_data=_callback("restart", 1),
            )
        ],
        [
            InlineKeyboardButton(
                text=_label("contact_master", language),
                callback_data="pf:contact",
            )
        ],
    ]


def _back_button(callback_data: str, language: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=_label("back", language), callback_data=callback_data)


def _category_for_partner(*, partner: Partner, category_id: UUID) -> ServiceCategory:
    try:
        return ServiceCategory.objects.get(partner=partner, id=category_id)
    except ServiceCategory.DoesNotExist as exc:
        raise ValidationError({"category": "Category is not available."}) from exc


def _active_service_for_partner(*, partner: Partner, service_id: UUID) -> Service:
    try:
        return active_services_for_booking(partner).select_related("category").get(id=service_id)
    except Service.DoesNotExist as exc:
        raise ValidationError({"service": "Service is not available."}) from exc


def _schedule_settings(partner: Partner) -> PartnerScheduleSettings:
    settings, _created = PartnerScheduleSettings.objects.get_or_create(partner=partner)
    return settings


def _find_slot_for_final_confirm(
    *,
    partner: Partner,
    selected_service_ids: Iterable[UUID | str],
    local_date: date,
    start_at: datetime,
    now: datetime | None,
):
    page_number = 1
    while True:
        slot_page = available_booking_slots(
            partner=partner,
            selected_service_ids=selected_service_ids,
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


def _summary_context(
    *,
    summary: BookingSummary,
    language: str,
    visible_lines: Iterable[BookingServiceLine] | None = None,
    page_indicator: str = "",
) -> dict[str, str]:
    services = _services_label(visible_lines or summary.service_lines)
    if page_indicator:
        services = f"{services}\n{page_indicator}"
    return {
        "services": services,
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


def _services_label(lines: Iterable[BookingServiceLine]) -> str:
    return "\n".join(line.name for line in lines)


def _appointment_services_label(appointment) -> str:
    return ", ".join(item.service_name_snapshot for item in appointment.service_items.all())


def _service_button_label(service, language: str, *, selected: bool) -> str:
    marker = "✓" if selected else "○"
    return (
        f"{marker} {service.name} · "
        f"{_duration_label(service.duration_minutes, language)} · "
        f"{_money_label(service.price_amount, service.currency)} {service.currency}"
    )


def _date_button_label(local_date: date, slot_count: int, language: str) -> str:
    return f"{local_date.isoformat()} · {slot_count} {_label('slots', language)}"


def _month_button_label(month_start: date, date_count: int, language: str) -> str:
    return (
        f"{_month_name(month_start.month, language)} {month_start.year} · "
        f"{date_count} {_available_dates_label(date_count, language)}"
    )


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


def _month_token(value: date) -> str:
    return value.strftime("%Y%m")


def _next_month(month_start: date) -> date:
    if month_start.month == 12:
        return date(month_start.year + 1, 1, 1)
    return date(month_start.year, month_start.month + 1, 1)


def validate_date_source_token(*, date_source: str, local_date: date) -> None:
    """Validate a callback return target without trusting callback state."""

    if date_source == NEAREST_DATE_SOURCE:
        return
    if parse_month_token(date_source) != local_date.replace(day=1):
        raise ValidationError({"callback": "Date does not belong to the selected month."})


def _date_list_callback(date_source: str) -> str:
    if date_source == NEAREST_DATE_SOURCE:
        return _callback("dates", 1)
    parse_month_token(date_source)
    return _callback("month", date_source, 1)


def _coerce_service_ids(values: Iterable[UUID | str]) -> tuple[UUID, ...]:
    service_ids: list[UUID] = []
    seen: set[UUID] = set()
    for value in values:
        try:
            service_id = value if isinstance(value, UUID) else UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise ValidationError({"services": "Invalid service selection."}) from exc
        if service_id not in seen:
            service_ids.append(service_id)
            seen.add(service_id)
    return tuple(service_ids)


def _label(key: str, language: str) -> str:
    labels = {
        "back": {"uk": "Назад", "ru": "Назад", "en": "Back"},
        "choose_date": {"uk": "Обрати дату", "ru": "Выбрать дату", "en": "Choose date"},
        "choose_month": {
            "uk": "Обрати місяць",
            "ru": "Выбрать месяц",
            "en": "Choose month",
        },
        "change_services": {
            "uk": "Змінити послуги",
            "ru": "Изменить услуги",
            "en": "Change services",
        },
        "clear": {"uk": "Очистити вибір", "ru": "Очистить выбор", "en": "Clear selection"},
        "add_comment": {
            "uk": "Додати коментар",
            "ru": "Добавить комментарий",
            "en": "Add comment",
        },
        "change_comment": {
            "uk": "Змінити коментар",
            "ru": "Изменить комментарий",
            "en": "Change comment",
        },
        "confirm": {"uk": "Підтвердити", "ru": "Подтвердить", "en": "Confirm"},
        "contact_master": {
            "uk": "Написати майстру",
            "ru": "Написать мастеру",
            "en": "Message master",
        },
        "done": {"uk": "Готово", "ru": "Готово", "en": "Done"},
        "remove": {"uk": "Прибрати", "ru": "Убрать", "en": "Remove"},
        "remove_comment": {
            "uk": "Прибрати коментар",
            "ru": "Убрать комментарий",
            "en": "Remove comment",
        },
        "phone_number": {
            "uk": "номер телефону одним текстовим повідомленням",
            "ru": "номер телефона одним текстовым сообщением",
            "en": "your phone number as one text message",
        },
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


def _booking_comment_line(comment: str, language: str) -> str:
    if not comment:
        return ""
    labels = {"uk": "Коментар", "ru": "Комментарий", "en": "Comment"}
    return f"\n{labels.get(language, labels['uk'])}: {comment}"


def _available_dates_label(count: int, language: str) -> str:
    if language == "en":
        return "available date" if count == 1 else "available dates"

    last_digit = count % 10
    last_two_digits = count % 100
    if language == "ru":
        if last_digit == 1 and last_two_digits != 11:
            return "доступная дата"
        if 2 <= last_digit <= 4 and not 12 <= last_two_digits <= 14:
            return "доступные даты"
        return "доступных дат"

    if last_digit == 1 and last_two_digits != 11:
        return "доступна дата"
    if 2 <= last_digit <= 4 and not 12 <= last_two_digits <= 14:
        return "доступні дати"
    return "доступних дат"


def _month_name(month: int, language: str) -> str:
    names = {
        "uk": (
            "Січень",
            "Лютий",
            "Березень",
            "Квітень",
            "Травень",
            "Червень",
            "Липень",
            "Серпень",
            "Вересень",
            "Жовтень",
            "Листопад",
            "Грудень",
        ),
        "ru": (
            "Январь",
            "Февраль",
            "Март",
            "Апрель",
            "Май",
            "Июнь",
            "Июль",
            "Август",
            "Сентябрь",
            "Октябрь",
            "Ноябрь",
            "Декабрь",
        ),
        "en": (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
    }
    language_names = names.get(language, names["uk"])
    return language_names[month - 1]
