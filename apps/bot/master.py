from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from django.core.exceptions import ValidationError

from apps.accounts.models import User
from apps.appointments.models import Appointment
from apps.appointments.selectors import appointment_for_partner_by_id
from apps.appointments.services import (
    confirm_requested_appointment,
    transition_appointment_status,
)
from apps.partners.models import Partner
from core.timezones import to_partner_time

from .services import render_content_template

MASTER_REQUEST_CALLBACK_PREFIX = "mr"


@dataclass(frozen=True, slots=True)
class BotMasterReply:
    """Text and inline keyboard returned to a verified master callback."""

    text: str
    reply_markup: InlineKeyboardMarkup | None = None


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
            ]
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


def _callback(action: str, appointment_id: UUID) -> str:
    return f"{MASTER_REQUEST_CALLBACK_PREFIX}:{action}:{appointment_id.hex}"


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
        "near_fit": {
            "uk": "Час трохи виходить за межі доступного вікна.",
            "ru": "Время немного выходит за границы доступного окна.",
            "en": "The time slightly exceeds the availability window.",
        },
    }
    translations = labels[key]
    return translations.get(language, translations["uk"])
