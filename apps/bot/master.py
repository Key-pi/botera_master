from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from datetime import timezone as datetime_timezone
from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.appointments.client_booking import (
    BookingSlotOption,
    available_requested_alternative_dates,
    available_requested_alternative_slots,
    create_requested_alternative_proposal,
)
from apps.appointments.models import Appointment
from apps.appointments.selectors import appointment_for_partner_by_id
from apps.appointments.services import (
    confirm_requested_appointment,
    transition_appointment_status,
)
from apps.partners.models import Partner
from apps.schedule.models import PartnerScheduleSettings
from core.timezones import to_partner_time

from .services import render_content_template

MASTER_REQUEST_CALLBACK_PREFIX = "mr"
MASTER_PROPOSAL_SELECTION_LIMIT = 5


@dataclass(frozen=True, slots=True)
class BotMasterReply:
    """Text and inline keyboard returned to a verified master callback."""

    text: str
    reply_markup: InlineKeyboardMarkup | None = None


@dataclass(frozen=True, slots=True)
class MasterProposalSelectionResult:
    """Refreshed master reply plus the validated ephemeral slot selection."""

    reply: BotMasterReply
    selected_start_ats: tuple[datetime, ...]


def master_booking_request_markup(
    *,
    appointment_id: UUID,
    language: str,
) -> InlineKeyboardMarkup:
    """Build compact actions for an actionable requested appointment."""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_label("confirm", language),
                    callback_data=_callback("confirm", appointment_id),
                ),
                InlineKeyboardButton(
                    text=_label("decline", language),
                    callback_data=_callback("decline", appointment_id),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_label("propose", language),
                    callback_data=_callback("propose", appointment_id),
                )
            ],
        ]
    )


def build_master_booking_request_reply(
    *,
    partner: Partner,
    appointment_id: UUID,
) -> BotMasterReply:
    """Render the current request after reloading it in trusted partner scope."""

    appointment = _requested_appointment(partner=partner, appointment_id=appointment_id)
    return BotMasterReply(
        text=_render_request(appointment),
        reply_markup=master_booking_request_markup(
            appointment_id=appointment.id,
            language=partner.default_language,
        ),
    )


def build_master_booking_request_decline_reply(
    *,
    partner: Partner,
    appointment_id: UUID,
) -> BotMasterReply:
    """Ask for an explicit second click before declining a client request."""

    appointment = _requested_appointment(partner=partner, appointment_id=appointment_id)
    return BotMasterReply(
        text=_render(
            partner=partner,
            key="master.booking_request_decline_confirm",
            context=_appointment_context(appointment),
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=_label("confirm_decline", partner.default_language),
                        callback_data=_callback("declinego", appointment.id),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=_label("back", partner.default_language),
                        callback_data=_callback("view", appointment.id),
                    )
                ],
            ]
        ),
    )


def confirm_master_booking_request_reply(
    *,
    partner: Partner,
    actor: User,
    appointment_id: UUID,
) -> BotMasterReply:
    """Confirm a request through the appointment owner service."""

    appointment = _requested_appointment(partner=partner, appointment_id=appointment_id)
    stored = confirm_requested_appointment(actor=actor, appointment=appointment)
    return BotMasterReply(
        text=_render(
            partner=partner,
            key="master.booking_request_confirmed",
            context=_appointment_context(stored),
        )
    )


def decline_master_booking_request_reply(
    *,
    partner: Partner,
    actor: User,
    appointment_id: UUID,
) -> BotMasterReply:
    """Decline a request without inventing a mandatory client-visible reason."""

    appointment = _requested_appointment(partner=partner, appointment_id=appointment_id)
    stored = transition_appointment_status(
        actor=actor,
        appointment=appointment,
        new_status=Appointment.Status.CANCELLED_BY_MASTER,
    )
    return BotMasterReply(
        text=_render(
            partner=partner,
            key="master.booking_request_declined",
            context=_appointment_context(stored),
        )
    )


def build_master_requested_alternative_dates_reply(
    *,
    partner: Partner,
    actor: User,
    appointment_id: UUID,
    selected_start_ats: Iterable[datetime] = (),
    page: int = 1,
    now: datetime | None = None,
) -> BotMasterReply:
    """Render available dates while retaining the master's selected alternatives."""

    appointment = _requested_appointment(partner=partner, appointment_id=appointment_id)
    selected = _normalize_selected_start_ats(selected_start_ats)
    current_time = now or timezone.now()
    settings, _created = PartnerScheduleSettings.objects.get_or_create(partner=partner)
    start_date = to_partner_time(current_time, partner.timezone).date()
    dates_page = available_requested_alternative_dates(
        actor=actor,
        partner=partner,
        appointment=appointment,
        start_date=start_date,
        days=settings.booking_horizon_days,
        page=page,
        now=current_time,
    )
    language = partner.default_language
    if not dates_page.items:
        return BotMasterReply(
            text=_render(
                partner=partner,
                key="master.booking_request_proposal_no_dates",
                context=_appointment_context(appointment),
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[_back_button(appointment.id, language)]]
            ),
        )

    rows = [
        [
            InlineKeyboardButton(
                text=_date_label(option.local_date, option.slot_count, language),
                callback_data=_callback(
                    "pslots",
                    appointment.id,
                    option.local_date.strftime("%Y%m%d"),
                    1,
                ),
            )
        ]
        for option in dates_page.items
    ]
    rows.extend(
        _proposal_pagination_rows(
            action="pdates",
            appointment_id=appointment.id,
            page=dates_page,
        )
    )
    rows.extend(
        _proposal_selection_rows(
            appointment_id=appointment.id,
            selected_count=len(selected),
            language=language,
        )
    )
    rows.append([_back_button(appointment.id, language)])
    return BotMasterReply(
        text=_render(
            partner=partner,
            key="master.booking_request_proposal_dates",
            context=_proposal_selection_context(appointment=appointment, selected=selected),
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def build_master_requested_alternative_slots_reply(
    *,
    partner: Partner,
    actor: User,
    appointment_id: UUID,
    local_date: date,
    selected_start_ats: Iterable[datetime] = (),
    page: int = 1,
    now: datetime | None = None,
    selection_limit_reached: bool = False,
) -> BotMasterReply:
    """Render toggleable alternative slots for one local date."""

    appointment = _requested_appointment(partner=partner, appointment_id=appointment_id)
    selected = _normalize_selected_start_ats(selected_start_ats)
    slots_page = available_requested_alternative_slots(
        actor=actor,
        partner=partner,
        appointment=appointment,
        local_date=local_date,
        page=page,
        now=now,
    )
    language = partner.default_language
    dates_callback = _callback("pdates", appointment.id, 1)
    if not slots_page.items:
        return BotMasterReply(
            text=_render(
                partner=partner,
                key="master.booking_request_proposal_no_slots",
                context={"date": local_date.isoformat()},
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=_label("choose_another_date", language),
                            callback_data=dates_callback,
                        )
                    ]
                ]
            ),
        )

    selected_set = set(selected)
    rows = [
        [
            InlineKeyboardButton(
                text=_slot_label(
                    slot=slot,
                    timezone_name=partner.timezone,
                    language=language,
                    selected=slot.start_at in selected_set,
                ),
                callback_data=_callback(
                    "ptoggle",
                    appointment.id,
                    _timestamp_token(slot.start_at),
                    slots_page.page,
                ),
            )
        ]
        for slot in slots_page.items
    ]
    rows.extend(
        _proposal_pagination_rows(
            action="pslots",
            appointment_id=appointment.id,
            page=slots_page,
            extra_parts=(local_date.strftime("%Y%m%d"),),
        )
    )
    rows.extend(
        _proposal_selection_rows(
            appointment_id=appointment.id,
            selected_count=len(selected),
            language=language,
        )
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=_label("choose_another_date", language),
                callback_data=dates_callback,
            )
        ]
    )
    text = _render(
        partner=partner,
        key="master.booking_request_proposal_slots",
        context=_proposal_selection_context(appointment=appointment, selected=selected)
        | {"date": local_date.isoformat()},
    )
    if selection_limit_reached:
        text = f"{_label('selection_limit_reached', language)}\n\n{text}"
    return BotMasterReply(
        text=text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def toggle_master_requested_alternative_slot_reply(
    *,
    partner: Partner,
    actor: User,
    appointment_id: UUID,
    start_at: datetime,
    selected_start_ats: Iterable[datetime],
    page: int = 1,
    now: datetime | None = None,
) -> MasterProposalSelectionResult:
    """Toggle one freshly validated slot in the master's ephemeral selection."""

    appointment = _requested_appointment(partner=partner, appointment_id=appointment_id)
    selected = list(_normalize_selected_start_ats(selected_start_ats))
    normalized_start = _normalize_aware_datetime(start_at)
    local_date = to_partner_time(normalized_start, partner.timezone).date()
    limit_reached = False
    if normalized_start in selected:
        selected.remove(normalized_start)
    elif len(selected) >= MASTER_PROPOSAL_SELECTION_LIMIT:
        limit_reached = True
    else:
        _find_requested_alternative_slot(
            actor=actor,
            partner=partner,
            appointment=appointment,
            start_at=normalized_start,
            now=now,
        )
        selected.append(normalized_start)
        selected.sort()

    reply = build_master_requested_alternative_slots_reply(
        partner=partner,
        actor=actor,
        appointment_id=appointment.id,
        local_date=local_date,
        selected_start_ats=selected,
        page=page,
        now=now,
        selection_limit_reached=limit_reached,
    )
    return MasterProposalSelectionResult(reply=reply, selected_start_ats=tuple(selected))


def build_master_requested_alternative_review_reply(
    *,
    partner: Partner,
    actor: User,
    appointment_id: UUID,
    selected_start_ats: Iterable[datetime],
    now: datetime | None = None,
) -> BotMasterReply:
    """Freshly validate and summarize alternatives before sending them."""

    appointment = _requested_appointment(partner=partner, appointment_id=appointment_id)
    selected = _normalize_selected_start_ats(selected_start_ats)
    slots = _validated_requested_alternative_slots(
        actor=actor,
        partner=partner,
        appointment=appointment,
        selected_start_ats=selected,
        now=now,
    )
    option_lines = "\n".join(
        f"{index}. {_proposal_slot_summary(slot, partner.timezone, partner.default_language)}"
        for index, slot in enumerate(slots, start=1)
    )
    language = partner.default_language
    return BotMasterReply(
        text=_render(
            partner=partner,
            key="master.booking_request_proposal_review",
            context=_appointment_context(appointment) | {"options": option_lines},
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=_label("send_proposal", language),
                        callback_data=_callback("psend", appointment.id),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=_label("change_selection", language),
                        callback_data=_callback("pdates", appointment.id, 1),
                    )
                ],
                [_back_button(appointment.id, language)],
            ]
        ),
    )


def send_master_requested_alternative_proposal_reply(
    *,
    partner: Partner,
    actor: User,
    appointment_id: UUID,
    selected_start_ats: Iterable[datetime],
    now: datetime | None = None,
) -> BotMasterReply:
    """Create and notify the client about the master's selected alternatives."""

    appointment = _requested_appointment(partner=partner, appointment_id=appointment_id)
    selected = _normalize_selected_start_ats(selected_start_ats)
    proposal = create_requested_alternative_proposal(
        actor=actor,
        partner=partner,
        appointment=appointment,
        selected_start_ats=selected,
        now=now,
    )
    return BotMasterReply(
        text=_render(
            partner=partner,
            key="master.booking_request_proposal_sent",
            context=_appointment_context(appointment)
            | {"option_count": str(proposal.options.count())},
        )
    )


def build_master_booking_request_action_failed_reply(
    *,
    partner: Partner,
    appointment_id: UUID | None = None,
) -> BotMasterReply:
    """Return a safe stale/conflict response without exposing technical details."""

    markup = None
    appointment = None
    if appointment_id is not None:
        try:
            appointment = _appointment_for_partner(
                partner=partner,
                appointment_id=appointment_id,
            )
        except ValidationError:
            appointment = None
    if appointment is not None and appointment.status == Appointment.Status.REQUESTED:
        markup = master_booking_request_markup(
            appointment_id=appointment.id,
            language=partner.default_language,
        )
    return BotMasterReply(
        text=_render(
            partner=partner,
            key="master.booking_request_action_failed",
            context={},
        ),
        reply_markup=markup,
    )


def parse_master_request_appointment_id(value: str) -> UUID:
    """Parse an opaque callback identifier without deriving tenant scope from it."""

    try:
        return UUID(hex=value)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"callback": "Invalid appointment identifier."}) from exc


def parse_master_proposal_date_token(value: str) -> date:
    """Parse a compact local date from a master callback."""

    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except (TypeError, ValueError) as exc:
        raise ValidationError({"callback": "Invalid proposal date."}) from exc


def parse_master_proposal_page_token(value: str) -> int:
    """Parse a positive Telegram page number."""

    try:
        page = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"callback": "Invalid proposal page."}) from exc
    if page < 1:
        raise ValidationError({"callback": "Invalid proposal page."})
    return page


def parse_master_proposal_timestamp_token(value: str) -> datetime:
    """Parse a UTC Unix timestamp from a master callback or FSM payload."""

    try:
        return datetime.fromtimestamp(int(value), tz=datetime_timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError) as exc:
        raise ValidationError({"callback": "Invalid proposal time."}) from exc


def _requested_appointment(*, partner: Partner, appointment_id: UUID) -> Appointment:
    appointment = _appointment_for_partner(partner=partner, appointment_id=appointment_id)
    if appointment.status != Appointment.Status.REQUESTED:
        raise ValidationError({"appointment": "Booking request is no longer pending."})
    return appointment


def _appointment_for_partner(*, partner: Partner, appointment_id: UUID) -> Appointment:
    appointment = appointment_for_partner_by_id(
        partner=partner,
        appointment_id=appointment_id,
    )
    if appointment is None:
        raise ValidationError({"appointment": "Booking request is unavailable."})
    return appointment


def _render_request(appointment: Appointment) -> str:
    context = _appointment_context(appointment)
    language = appointment.partner.default_language
    context["fit_note"] = f"\n{_label('near_fit', language)}" if appointment.is_near_fit else ""
    context["comment"] = (
        f"\n{_label('comment', language)}: {appointment.client_comment.strip()}"
        if appointment.client_comment.strip()
        else ""
    )
    return _render(
        partner=appointment.partner,
        key="master.booking_request",
        context=context,
    )


def _appointment_context(appointment: Appointment) -> dict[str, str]:
    local_start = to_partner_time(appointment.start_at, appointment.partner.timezone)
    services = ", ".join(item.service_name_snapshot for item in appointment.service_items.all())
    return {
        "client_name": appointment.client_profile.display_name,
        "client_phone": appointment.client_profile.phone,
        "date": local_start.date().isoformat(),
        "time": local_start.strftime("%H:%M"),
        "services": services,
    }


def _render(*, partner: Partner, key: str, context: dict[str, str]) -> str:
    return render_content_template(
        partner=partner,
        key=key,
        language=partner.default_language,
        context=context,
    ).body


def _callback(action: str, appointment_id: UUID, *parts: object) -> str:
    callback_parts = [MASTER_REQUEST_CALLBACK_PREFIX, action, appointment_id.hex]
    callback_parts.extend(str(part) for part in parts)
    return ":".join(callback_parts)


def _proposal_pagination_rows(
    *,
    action: str,
    appointment_id: UUID,
    page,
    extra_parts: tuple[object, ...] = (),
) -> list[list[InlineKeyboardButton]]:
    if not page.has_previous and not page.has_next:
        return []
    row: list[InlineKeyboardButton] = []
    if page.has_previous:
        row.append(
            InlineKeyboardButton(
                text="<",
                callback_data=_callback(
                    action,
                    appointment_id,
                    *extra_parts,
                    page.page - 1,
                ),
            )
        )
    row.append(InlineKeyboardButton(text=page.indicator, callback_data="mr:noop"))
    if page.has_next:
        row.append(
            InlineKeyboardButton(
                text=">",
                callback_data=_callback(
                    action,
                    appointment_id,
                    *extra_parts,
                    page.page + 1,
                ),
            )
        )
    return [row]


def _proposal_selection_rows(
    *,
    appointment_id: UUID,
    selected_count: int,
    language: str,
) -> list[list[InlineKeyboardButton]]:
    if selected_count <= 0:
        return []
    return [
        [
            InlineKeyboardButton(
                text=f"{_label('review_selection', language)} ({selected_count})",
                callback_data=_callback("preview", appointment_id),
            )
        ]
    ]


def _back_button(appointment_id: UUID, language: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=_label("back", language),
        callback_data=_callback("view", appointment_id),
    )


def _date_label(local_date: date, slot_count: int, language: str) -> str:
    return f"{local_date.isoformat()} · {slot_count} {_label('slots', language)}"


def _slot_label(
    *,
    slot: BookingSlotOption,
    timezone_name: str,
    language: str,
    selected: bool,
) -> str:
    marker = "✓" if selected else "○"
    label = to_partner_time(slot.start_at, timezone_name).strftime("%H:%M")
    if slot.is_near_fit:
        label = f"{label} · {_label('near_fit_short', language)}"
    return f"{marker} {label}"


def _proposal_slot_summary(
    slot: BookingSlotOption,
    timezone_name: str,
    language: str,
) -> str:
    local_start = to_partner_time(slot.start_at, timezone_name)
    summary = local_start.strftime("%Y-%m-%d %H:%M")
    if slot.is_near_fit:
        summary = f"{summary} · {_label('near_fit_short', language)}"
    return summary


def _proposal_selection_context(
    *,
    appointment: Appointment,
    selected: tuple[datetime, ...],
) -> dict[str, str]:
    return _appointment_context(appointment) | {
        "selected_count": str(len(selected)),
        "selection_limit": str(MASTER_PROPOSAL_SELECTION_LIMIT),
    }


def _validated_requested_alternative_slots(
    *,
    actor: User,
    partner: Partner,
    appointment: Appointment,
    selected_start_ats: tuple[datetime, ...],
    now: datetime | None,
) -> tuple[BookingSlotOption, ...]:
    if not selected_start_ats:
        raise ValidationError({"options": "Select at least one alternative time."})
    current_time = now or timezone.now()
    return tuple(
        _find_requested_alternative_slot(
            actor=actor,
            partner=partner,
            appointment=appointment,
            start_at=start_at,
            now=current_time,
        )
        for start_at in selected_start_ats
    )


def _find_requested_alternative_slot(
    *,
    actor: User,
    partner: Partner,
    appointment: Appointment,
    start_at: datetime,
    now: datetime | None,
) -> BookingSlotOption:
    local_date = to_partner_time(start_at, partner.timezone).date()
    page_number = 1
    while True:
        slot_page = available_requested_alternative_slots(
            actor=actor,
            partner=partner,
            appointment=appointment,
            local_date=local_date,
            page=page_number,
            now=now,
        )
        for slot in slot_page.items:
            if slot.start_at == start_at:
                return slot
        if not slot_page.has_next:
            raise ValidationError({"start_at": "Selected alternative is no longer available."})
        page_number += 1


def _normalize_selected_start_ats(values: Iterable[datetime]) -> tuple[datetime, ...]:
    selected: list[datetime] = []
    seen: set[datetime] = set()
    for value in values:
        normalized = _normalize_aware_datetime(value)
        if normalized not in seen:
            seen.add(normalized)
            selected.append(normalized)
    if len(selected) > MASTER_PROPOSAL_SELECTION_LIMIT:
        raise ValidationError({"options": "Too many proposal alternatives."})
    return tuple(sorted(selected))


def _normalize_aware_datetime(value: datetime) -> datetime:
    if timezone.is_naive(value):
        raise ValidationError({"start_at": "Proposal time must be timezone-aware."})
    return value.astimezone(datetime_timezone.utc)


def _timestamp_token(value: datetime) -> int:
    return int(value.astimezone(datetime_timezone.utc).timestamp())


def _label(key: str, language: str) -> str:
    labels = {
        "back": {"uk": "Назад", "ru": "Назад", "en": "Back"},
        "comment": {"uk": "Коментар", "ru": "Комментарий", "en": "Comment"},
        "confirm": {"uk": "Підтвердити", "ru": "Подтвердить", "en": "Confirm"},
        "confirm_decline": {
            "uk": "Так, відхилити",
            "ru": "Да, отклонить",
            "en": "Yes, decline",
        },
        "decline": {"uk": "Відхилити", "ru": "Отклонить", "en": "Decline"},
        "propose": {
            "uk": "Запропонувати інший час",
            "ru": "Предложить другое время",
            "en": "Propose another time",
        },
        "choose_another_date": {
            "uk": "Обрати іншу дату",
            "ru": "Выбрать другую дату",
            "en": "Choose another date",
        },
        "change_selection": {
            "uk": "Змінити варіанти",
            "ru": "Изменить варианты",
            "en": "Change options",
        },
        "review_selection": {
            "uk": "Перевірити варіанти",
            "ru": "Проверить варианты",
            "en": "Review options",
        },
        "send_proposal": {
            "uk": "Надіслати клієнту",
            "ru": "Отправить клиенту",
            "en": "Send to client",
        },
        "slots": {"uk": "слотів", "ru": "слотов", "en": "slots"},
        "near_fit_short": {
            "uk": "поза вікном",
            "ru": "вне окна",
            "en": "outside window",
        },
        "selection_limit_reached": {
            "uk": "Можна запропонувати щонайбільше 5 варіантів.",
            "ru": "Можно предложить не больше 5 вариантов.",
            "en": "You can propose up to 5 options.",
        },
        "near_fit": {
            "uk": "Час трохи виходить за межі доступного вікна.",
            "ru": "Время немного выходит за границы доступного окна.",
            "en": "The time slightly exceeds the availability window.",
        },
    }
    translations = labels[key]
    return translations.get(language, translations["uk"])
