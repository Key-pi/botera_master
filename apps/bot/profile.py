from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.appointments.client_booking import (
    client_appointment_history_page,
    client_future_appointments_page,
    client_profile_overview,
)
from apps.appointments.models import Appointment
from apps.clients.models import ClientProfile
from apps.clients.services import SUPPORTED_CLIENT_LANGUAGES, update_client_profile_language
from apps.partners.models import Partner
from core.money import Money
from core.timezones import to_partner_time

from .services import render_content_template

PROFILE_CALLBACK_PREFIX = "pf"


@dataclass(frozen=True, slots=True)
class BotProfileReply:
    """Text and inline keyboard ready for a bot profile adapter response."""

    text: str
    reply_markup: InlineKeyboardMarkup | None = None


def build_profile_main_reply(
    *,
    partner: Partner,
    client_profile: ClientProfile,
    now: datetime | None = None,
) -> BotProfileReply:
    """Render the client's personal-area landing screen."""

    overview = client_profile_overview(client_profile=client_profile, now=now)
    language = client_profile.language
    text = _render(
        partner=partner,
        key="profile.main",
        language=language,
        context={
            "client_name": client_profile.display_name,
            "phone": client_profile.phone or _label("not_set", language),
            "language": _language_label(language),
            "visit_count": str(overview.visit_count),
            "extra_text": client_profile.extra_client_text,
        },
    )
    rows = [
        [
            InlineKeyboardButton(
                text=_label("future", language),
                callback_data=_callback("future", 1),
            )
        ],
        [
            InlineKeyboardButton(
                text=_label("history", language),
                callback_data=_callback("history", 1),
            )
        ],
        [
            InlineKeyboardButton(
                text=_label("language", language),
                callback_data=_callback("lang"),
            )
        ],
        [
            InlineKeyboardButton(
                text=_label("book", language),
                callback_data="bk:cat:1",
            )
        ],
    ]
    return BotProfileReply(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


def build_profile_language_reply(
    *,
    partner: Partner,
    client_profile: ClientProfile,
) -> BotProfileReply:
    """Render language choices for the partner-scoped client profile."""

    language = client_profile.language
    rows = [
        [
            InlineKeyboardButton(
                text=_language_label(choice),
                callback_data=_callback("langset", choice),
            )
        ]
        for choice in ("uk", "ru", "en")
        if choice in SUPPORTED_CLIENT_LANGUAGES
    ]
    rows.append([_back_button(_callback("main"), language)])
    return BotProfileReply(
        text=_render(partner=partner, key="start.choose_language", language=language),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def set_profile_language_reply(
    *,
    partner: Partner,
    client_profile: ClientProfile,
    language: str,
) -> BotProfileReply:
    """Update profile language and render a localized confirmation."""

    updated_profile = update_client_profile_language(
        client_profile=client_profile,
        language=language,
    )
    text = _render(
        partner=partner,
        key="profile.language_updated",
        language=updated_profile.language,
        context={"language": _language_label(updated_profile.language)},
    )
    main = build_profile_main_reply(partner=partner, client_profile=updated_profile)
    return BotProfileReply(text=f"{text}\n\n{main.text}", reply_markup=main.reply_markup)


def build_future_appointments_reply(
    *,
    partner: Partner,
    client_profile: ClientProfile,
    page: int = 1,
    now: datetime | None = None,
) -> BotProfileReply:
    """Render future requested/confirmed appointments with Telegram-safe pagination."""

    language = client_profile.language
    appointment_page = client_future_appointments_page(
        client_profile=client_profile,
        page=page,
        now=now or timezone.now(),
    )
    if not appointment_page.items:
        return _reply_with_back(
            partner=partner,
            key="profile.future_appointments_empty",
            language=language,
        )
    return _appointment_list_reply(
        partner=partner,
        language=language,
        key="profile.future_appointments_list",
        action="future",
        appointment_page=appointment_page,
    )


def build_appointment_history_reply(
    *,
    partner: Partner,
    client_profile: ClientProfile,
    page: int = 1,
) -> BotProfileReply:
    """Render newest-to-oldest appointment history for the client."""

    language = client_profile.language
    appointment_page = client_appointment_history_page(
        client_profile=client_profile,
        page=page,
    )
    if not appointment_page.items:
        return _reply_with_back(
            partner=partner,
            key="profile.history_empty",
            language=language,
        )
    return _appointment_list_reply(
        partner=partner,
        language=language,
        key="profile.history_list",
        action="history",
        appointment_page=appointment_page,
    )


def build_appointment_details_reply(
    *,
    partner: Partner,
    client_profile: ClientProfile,
    appointment_id: UUID,
    return_action: str = "history",
    return_page: int = 1,
) -> BotProfileReply:
    """Render full details for one client-owned appointment."""

    try:
        appointment = (
            Appointment.objects.filter(
                partner=partner,
                client_profile=client_profile,
                id=appointment_id,
            )
            .prefetch_related("service_items")
            .get()
        )
    except Appointment.DoesNotExist as exc:
        raise ValidationError({"appointment": "Appointment is not available."}) from exc

    language = client_profile.language
    text = _render(
        partner=partner,
        key="profile.appointment_details",
        language=language,
        context=_appointment_details_context(
            partner=partner,
            appointment=appointment,
            language=language,
        ),
    )
    rows = [[_back_button(_callback(return_action, return_page), language)]]
    return BotProfileReply(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


def parse_uuid_token(value: str) -> UUID:
    """Parse compact UUID callback token."""

    try:
        return UUID(hex=value)
    except ValueError as exc:
        raise ValidationError({"callback": "Invalid callback identifier."}) from exc


def parse_page_token(value: str) -> int:
    """Parse a positive callback page number."""

    try:
        return max(1, int(value))
    except (TypeError, ValueError) as exc:
        raise ValidationError({"callback": "Invalid callback page."}) from exc


def _appointment_list_reply(
    *,
    partner: Partner,
    language: str,
    key: str,
    action: str,
    appointment_page,
) -> BotProfileReply:
    appointments = "\n".join(
        _appointment_short_line(partner=partner, appointment=appointment, language=language)
        for appointment in appointment_page.items
    )
    rows = [
        [
            InlineKeyboardButton(
                text=f"{index}. {_label('details', language)}",
                callback_data=_callback(
                    "details",
                    appointment.id.hex,
                    action,
                    appointment_page.page,
                ),
            )
        ]
        for index, appointment in enumerate(appointment_page.items, start=1)
    ]
    rows.extend(_pagination_rows(action, appointment_page.page, appointment_page))
    rows.append([_back_button(_callback("main"), language)])
    return BotProfileReply(
        text=_render(
            partner=partner,
            key=key,
            language=language,
            context={"appointments": appointments},
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def _reply_with_back(*, partner: Partner, key: str, language: str) -> BotProfileReply:
    return BotProfileReply(
        text=_render(partner=partner, key=key, language=language),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[_back_button(_callback("main"), language)]],
        ),
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
    return ":".join([PROFILE_CALLBACK_PREFIX, *(str(part) for part in parts)])


def _pagination_rows(action: str, current_page: int, page):
    if not page.has_previous and not page.has_next:
        return []

    row: list[InlineKeyboardButton] = []
    if page.has_previous:
        row.append(
            InlineKeyboardButton(
                text="<",
                callback_data=_callback(action, current_page - 1),
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
                callback_data=_callback(action, current_page + 1),
            )
        )
    return [row]


def _back_button(callback_data: str, language: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=_label("back", language), callback_data=callback_data)


def _appointment_short_line(
    *,
    partner: Partner,
    appointment: Appointment,
    language: str,
) -> str:
    partner_time = to_partner_time(appointment.start_at, partner.timezone)
    return (
        f"{partner_time.date().isoformat()} {partner_time.strftime('%H:%M')} · "
        f"{_appointment_services_label(appointment)} · "
        f"{_status_label(appointment.status, language)}"
    )


def _appointment_details_context(
    *,
    partner: Partner,
    appointment: Appointment,
    language: str,
) -> dict[str, str]:
    partner_time = to_partner_time(appointment.start_at, partner.timezone)
    return {
        "date": partner_time.date().isoformat(),
        "time": partner_time.strftime("%H:%M"),
        "services": _appointment_services_label(appointment),
        "status": _status_label(appointment.status, language),
        "service_total": _money_label(appointment.service_total_amount),
        "penalties_total": _money_label(appointment.penalties_total_amount),
        "total": _money_label(appointment.total_amount),
        "currency": appointment.currency,
        "comment": _optional_line("comment", appointment.client_comment, language),
        "reason": _optional_line("reason", appointment.client_visible_reason, language),
    }


def _appointment_services_label(appointment: Appointment) -> str:
    return ", ".join(item.service_name_snapshot for item in appointment.service_items.all())


def _optional_line(key: str, value: str, language: str) -> str:
    cleaned_value = value.strip()
    if not cleaned_value:
        return ""
    return f"{_label(key, language)}: {cleaned_value}"


def _money_label(amount: int) -> str:
    return str(Money(amount=amount).to_major_decimal())


def _language_label(language: str) -> str:
    labels = {
        "uk": "Українська",
        "ru": "Русский",
        "en": "English",
    }
    return labels.get(language, labels["uk"])


def _status_label(status: str, language: str) -> str:
    labels = {
        Appointment.Status.REQUESTED: {
            "uk": "очікує підтвердження",
            "ru": "ждёт подтверждения",
            "en": "pending confirmation",
        },
        Appointment.Status.CONFIRMED: {
            "uk": "підтверджено",
            "ru": "подтверждено",
            "en": "confirmed",
        },
        Appointment.Status.CANCELLED_BY_CLIENT: {
            "uk": "скасовано клієнтом",
            "ru": "отменено клиентом",
            "en": "cancelled by client",
        },
        Appointment.Status.CANCELLED_BY_MASTER: {
            "uk": "скасовано майстром",
            "ru": "отменено мастером",
            "en": "cancelled by master",
        },
        Appointment.Status.COMPLETED: {
            "uk": "завершено",
            "ru": "завершено",
            "en": "completed",
        },
        Appointment.Status.NO_SHOW: {
            "uk": "не прийшов",
            "ru": "не пришёл",
            "en": "no-show",
        },
    }
    translations = labels[status]
    return translations.get(language, translations["uk"])


def _label(key: str, language: str) -> str:
    labels = {
        "back": {"uk": "Назад", "ru": "Назад", "en": "Back"},
        "book": {"uk": "Записатися", "ru": "Записаться", "en": "Book"},
        "comment": {"uk": "Коментар", "ru": "Комментарий", "en": "Comment"},
        "details": {"uk": "Деталі", "ru": "Детали", "en": "Details"},
        "future": {"uk": "Мої записи", "ru": "Мои записи", "en": "My bookings"},
        "history": {"uk": "Історія", "ru": "История", "en": "History"},
        "language": {"uk": "Мова", "ru": "Язык", "en": "Language"},
        "not_set": {"uk": "не вказано", "ru": "не указано", "en": "not set"},
        "reason": {"uk": "Причина", "ru": "Причина", "en": "Reason"},
    }
    translations = labels[key]
    return translations.get(language, translations["uk"])
