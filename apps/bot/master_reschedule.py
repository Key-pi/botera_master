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
from apps.accounts.services import ensure_partner_access
from apps.appointments.client_booking import (
    BookingSlotOption,
    available_master_reschedule_dates,
    available_master_reschedule_slots,
    create_master_reschedule_proposal,
)
from apps.appointments.models import Appointment
from apps.appointments.selectors import appointment_for_partner_by_id
from apps.partners.models import Partner
from apps.schedule.models import PartnerScheduleSettings
from core.timezones import to_partner_time

from .master import MASTER_PROPOSAL_SELECTION_LIMIT
from .services import render_content_template

MASTER_RESCHEDULE_CALLBACK_PREFIX = "rs"


@dataclass(frozen=True, slots=True)
class BotMasterRescheduleReply:
    """Text and keyboard for one master-initiated reschedule step."""

    text: str
    reply_markup: InlineKeyboardMarkup | None = None


@dataclass(frozen=True, slots=True)
class MasterRescheduleSelectionResult:
    """Refreshed reply plus the master's bounded ephemeral slot selection."""

    reply: BotMasterRescheduleReply
    selected_start_ats: tuple[datetime, ...]


def master_reschedule_start_callback(appointment_id: UUID) -> str:
    """Return the compact entry callback used by appointment details."""

    return _callback("d", appointment_id, 1)


def build_master_reschedule_dates_reply(
    *,
    partner: Partner,
    actor: User,
    appointment_id: UUID,
    selected_start_ats: Iterable[datetime] = (),
    page: int = 1,
    now: datetime | None = None,
) -> BotMasterRescheduleReply:
    """Render available dates for a future confirmed appointment."""

    appointment = _appointment(
        partner=partner,
        actor=actor,
        appointment_id=appointment_id,
    )
    selected = _normalize_selected_start_ats(selected_start_ats)
    current_time = now or timezone.now()
    settings, _created = PartnerScheduleSettings.objects.get_or_create(partner=partner)
    start_date = to_partner_time(current_time, partner.timezone).date()
    dates_page = available_master_reschedule_dates(
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
        return BotMasterRescheduleReply(
            text=_render(
                partner=partner,
                key="master.reschedule_no_dates",
                context={"client_name": appointment.client_profile.display_name},
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[_appointment_back_button(appointment.id, language)]]
            ),
        )

    rows = [
        [
            InlineKeyboardButton(
                text=_date_label(option.local_date, option.slot_count, language),
                callback_data=_callback(
                    "s",
                    appointment.id,
                    option.local_date.strftime("%Y%m%d"),
                    1,
                ),
            )
        ]
        for option in dates_page.items
    ]
    rows.extend(
        _pagination_rows(
            action="d",
            appointment_id=appointment.id,
            page=dates_page,
        )
    )
    rows.extend(
        _selection_rows(
            appointment_id=appointment.id,
            selected_count=len(selected),
            language=language,
        )
    )
    rows.append([_appointment_back_button(appointment.id, language)])
    return BotMasterRescheduleReply(
        text=_render(
            partner=partner,
            key="master.reschedule_dates",
            context={
                "client_name": appointment.client_profile.display_name,
                "selected_count": str(len(selected)),
                "selection_limit": str(MASTER_PROPOSAL_SELECTION_LIMIT),
            },
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def build_master_reschedule_slots_reply(
    *,
    partner: Partner,
    actor: User,
    appointment_id: UUID,
    local_date: date,
    selected_start_ats: Iterable[datetime] = (),
    page: int = 1,
    now: datetime | None = None,
    selection_limit_reached: bool = False,
) -> BotMasterRescheduleReply:
    """Render toggleable replacement slots for one partner-local date."""

    appointment = _appointment(
        partner=partner,
        actor=actor,
        appointment_id=appointment_id,
    )
    selected = _normalize_selected_start_ats(selected_start_ats)
    slots_page = available_master_reschedule_slots(
        actor=actor,
        partner=partner,
        appointment=appointment,
        local_date=local_date,
        page=page,
        now=now,
    )
    language = partner.default_language
    dates_callback = _callback("d", appointment.id, 1)
    if not slots_page.items:
        return BotMasterRescheduleReply(
            text=_render(
                partner=partner,
                key="master.reschedule_no_slots",
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
    date_token = local_date.strftime("%Y%m%d")
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
                    "t",
                    appointment.id,
                    int(slot.start_at.timestamp()),
                    date_token,
                    slots_page.page,
                ),
            )
        ]
        for slot in slots_page.items
    ]
    rows.extend(
        _pagination_rows(
            action="s",
            appointment_id=appointment.id,
            page=slots_page,
            extra_parts=(date_token,),
        )
    )
    rows.extend(
        _selection_rows(
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
        key="master.reschedule_slots",
        context={
            "date": local_date.isoformat(),
            "selected_count": str(len(selected)),
            "selection_limit": str(MASTER_PROPOSAL_SELECTION_LIMIT),
        },
    )
    if selection_limit_reached:
        text = f"{_label('selection_limit_reached', language)}\n\n{text}"
    return BotMasterRescheduleReply(
        text=text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def toggle_master_reschedule_slot_reply(
    *,
    partner: Partner,
    actor: User,
    appointment_id: UUID,
    start_at: datetime,
    local_date: date,
    selected_start_ats: Iterable[datetime],
    page: int = 1,
    now: datetime | None = None,
) -> MasterRescheduleSelectionResult:
    """Toggle one freshly validated proposed slot in ephemeral Telegram state."""

    appointment = _appointment(
        partner=partner,
        actor=actor,
        appointment_id=appointment_id,
    )
    selected = list(_normalize_selected_start_ats(selected_start_ats))
    normalized_start = _normalize_aware_datetime(start_at)
    expected_date = to_partner_time(normalized_start, partner.timezone).date()
    if expected_date != local_date:
        raise ValidationError({"start_at": "Selected time does not match the date."})

    limit_reached = False
    if normalized_start in selected:
        selected.remove(normalized_start)
    elif len(selected) >= MASTER_PROPOSAL_SELECTION_LIMIT:
        limit_reached = True
    else:
        _find_available_slot(
            actor=actor,
            partner=partner,
            appointment=appointment,
            start_at=normalized_start,
            now=now,
        )
        selected.append(normalized_start)
        selected.sort()

    reply = build_master_reschedule_slots_reply(
        partner=partner,
        actor=actor,
        appointment_id=appointment.id,
        local_date=local_date,
        selected_start_ats=selected,
        page=page,
        now=now,
        selection_limit_reached=limit_reached,
    )
    return MasterRescheduleSelectionResult(
        reply=reply,
        selected_start_ats=tuple(selected),
    )


def build_master_reschedule_review_reply(
    *,
    partner: Partner,
    actor: User,
    appointment_id: UUID,
    selected_start_ats: Iterable[datetime],
    now: datetime | None = None,
) -> BotMasterRescheduleReply:
    """Freshly validate and summarize proposed replacement times."""

    appointment = _appointment(
        partner=partner,
        actor=actor,
        appointment_id=appointment_id,
    )
    selected = _normalize_selected_start_ats(selected_start_ats)
    slots = _validated_slots(
        actor=actor,
        partner=partner,
        appointment=appointment,
        selected_start_ats=selected,
        now=now,
    )
    language = partner.default_language
    option_lines = "\n".join(
        f"{index}. {_slot_summary(slot, partner.timezone, language)}"
        for index, slot in enumerate(slots, start=1)
    )
    return BotMasterRescheduleReply(
        text=_render(
            partner=partner,
            key="master.reschedule_review",
            context={
                "client_name": appointment.client_profile.display_name,
                "options": option_lines,
            },
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=_label("send_proposal", language),
                        callback_data=_callback("g", appointment.id),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=_label("change_selection", language),
                        callback_data=_callback("d", appointment.id, 1),
                    )
                ],
                [_appointment_back_button(appointment.id, language)],
            ]
        ),
    )


def send_master_reschedule_proposal_reply(
    *,
    partner: Partner,
    actor: User,
    appointment_id: UUID,
    selected_start_ats: Iterable[datetime],
    now: datetime | None = None,
) -> BotMasterRescheduleReply:
    """Persist and notify one master-initiated multi-option reschedule proposal."""

    appointment = _appointment(
        partner=partner,
        actor=actor,
        appointment_id=appointment_id,
    )
    selected = _normalize_selected_start_ats(selected_start_ats)
    proposal = create_master_reschedule_proposal(
        actor=actor,
        partner=partner,
        appointment=appointment,
        selected_start_ats=selected,
        now=now,
    )
    language = partner.default_language
    return BotMasterRescheduleReply(
        text=_render(
            partner=partner,
            key="master.reschedule_sent",
            context={
                "client_name": appointment.client_profile.display_name,
                "option_count": str(proposal.options.count()),
            },
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[_appointment_back_button(appointment.id, language)]]
        ),
    )


def build_master_reschedule_action_failed_reply(
    *,
    partner: Partner,
) -> BotMasterRescheduleReply:
    """Return a business-safe stale/conflict error for a callback failure."""

    return BotMasterRescheduleReply(
        text=_render(
            partner=partner,
            key="master.reschedule_action_failed",
        )
    )


def _appointment(
    *,
    partner: Partner,
    actor: User,
    appointment_id: UUID,
) -> Appointment:
    ensure_partner_access(actor, partner)
    appointment = appointment_for_partner_by_id(
        partner=partner,
        appointment_id=appointment_id,
    )
    if appointment is None:
        raise ValidationError({"appointment": "Appointment was not found."})
    if (
        not appointment.client_profile.telegram_identity_id
        or appointment.client_profile.bot_unreachable
    ):
        raise ValidationError({"client": "Client is unavailable through Telegram."})
    return appointment


def _validated_slots(
    *,
    actor: User,
    partner: Partner,
    appointment: Appointment,
    selected_start_ats: tuple[datetime, ...],
    now: datetime | None,
) -> tuple[BookingSlotOption, ...]:
    if not selected_start_ats:
        raise ValidationError({"options": "Select at least one replacement time."})
    return tuple(
        _find_available_slot(
            actor=actor,
            partner=partner,
            appointment=appointment,
            start_at=start_at,
            now=now,
        )
        for start_at in selected_start_ats
    )


def _find_available_slot(
    *,
    actor: User,
    partner: Partner,
    appointment: Appointment,
    start_at: datetime,
    now: datetime | None,
) -> BookingSlotOption:
    local_date = to_partner_time(start_at, partner.timezone).date()
    slots_page = available_master_reschedule_slots(
        actor=actor,
        partner=partner,
        appointment=appointment,
        local_date=local_date,
        page=1,
        per_page=10_000,
        now=now,
    )
    for slot in slots_page.items:
        if slot.start_at == start_at:
            return slot
    raise ValidationError({"start_at": "Selected replacement time is no longer available."})


def _normalize_selected_start_ats(values: Iterable[datetime]) -> tuple[datetime, ...]:
    normalized: list[datetime] = []
    seen: set[datetime] = set()
    for value in values:
        start_at = _normalize_aware_datetime(value)
        if start_at not in seen:
            seen.add(start_at)
            normalized.append(start_at)
    normalized.sort()
    return tuple(normalized)


def _normalize_aware_datetime(value: datetime) -> datetime:
    if timezone.is_naive(value):
        raise ValidationError({"start_at": "Datetime must be timezone-aware."})
    return value.astimezone(datetime_timezone.utc)


def _callback(action: str, appointment_id: UUID, *parts: object) -> str:
    values = [MASTER_RESCHEDULE_CALLBACK_PREFIX, action, appointment_id.hex]
    values.extend(str(part) for part in parts)
    return ":".join(values)


def _pagination_rows(
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
    row.append(
        InlineKeyboardButton(
            text=page.indicator,
            callback_data=f"{MASTER_RESCHEDULE_CALLBACK_PREFIX}:noop",
        )
    )
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


def _selection_rows(
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
                callback_data=_callback("v", appointment_id),
            )
        ]
    ]


def _appointment_back_button(
    appointment_id: UUID,
    language: str,
) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=_label("back", language),
        callback_data=f"mw:view:{appointment_id.hex}:u:1",
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


def _slot_summary(
    slot: BookingSlotOption,
    timezone_name: str,
    language: str,
) -> str:
    local_start = to_partner_time(slot.start_at, timezone_name)
    summary = local_start.strftime("%Y-%m-%d %H:%M")
    if slot.is_near_fit:
        summary = f"{summary} · {_label('near_fit_short', language)}"
    return summary


def _render(
    *,
    partner: Partner,
    key: str,
    context: dict[str, str] | None = None,
) -> str:
    return render_content_template(
        partner=partner,
        key=key,
        language=partner.default_language,
        context=context,
    ).body


def _label(key: str, language: str) -> str:
    labels = {
        "slots": {"uk": "вікон", "ru": "окон", "en": "slots"},
        "near_fit_short": {
            "uk": "потрібне підтвердження",
            "ru": "нужно подтверждение",
            "en": "confirmation required",
        },
        "review_selection": {
            "uk": "Перевірити вибір",
            "ru": "Проверить выбор",
            "en": "Review selection",
        },
        "choose_another_date": {
            "uk": "Інша дата",
            "ru": "Другая дата",
            "en": "Another date",
        },
        "send_proposal": {
            "uk": "Надіслати клієнту",
            "ru": "Отправить клиенту",
            "en": "Send to client",
        },
        "change_selection": {
            "uk": "Змінити вибір",
            "ru": "Изменить выбор",
            "en": "Change selection",
        },
        "selection_limit_reached": {
            "uk": "Можна вибрати не більше 5 варіантів.",
            "ru": "Можно выбрать не больше 5 вариантов.",
            "en": "You can select at most 5 options.",
        },
        "back": {"uk": "Назад", "ru": "Назад", "en": "Back"},
    }
    translations = labels[key]
    return translations.get(language, translations["uk"])
