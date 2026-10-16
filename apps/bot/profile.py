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
from apps.appointments.models import Appointment, AppointmentProposal, AppointmentProposalOption
from apps.appointments.proposals import (
    accept_appointment_proposal_option,
    decline_appointment_proposal,
)
from apps.appointments.services import (
    cancel_appointment_by_client,
    client_cancellation_policy,
)
from apps.clients.models import ClientProfile
from apps.clients.services import SUPPORTED_CLIENT_LANGUAGES, update_client_profile_language
from apps.messaging.services import create_client_message_to_master
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
                text=_label("contact_master", language),
                callback_data=_callback("contact"),
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


def build_contact_master_prompt_reply(
    *,
    partner: Partner,
    client_profile: ClientProfile,
    invalid_message: bool = False,
) -> BotProfileReply:
    """Prompt the client for one free-text message to the partner."""

    language = client_profile.language
    prompt = _render(
        partner=partner,
        key="profile.contact_master_prompt",
        language=language,
        context={"partner_name": partner.name},
    )
    if invalid_message:
        error = _render(
            partner=partner,
            key="profile.contact_master_invalid",
            language=language,
        )
        prompt = f"{error}\n\n{prompt}"
    return BotProfileReply(
        text=prompt,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[_back_button(_callback("main"), language)]],
        ),
    )


def send_client_message_to_master_reply(
    *,
    partner: Partner,
    client_profile: ClientProfile,
    text: str,
) -> BotProfileReply:
    """Queue a client message and return the personal-area confirmation."""

    create_client_message_to_master(
        partner=partner,
        client_profile=client_profile,
        text=text,
    )
    language = client_profile.language
    confirmation = _render(
        partner=partner,
        key="profile.contact_master_sent",
        language=language,
    )
    main = build_profile_main_reply(partner=partner, client_profile=client_profile)
    return BotProfileReply(
        text=f"{confirmation}\n\n{main.text}",
        reply_markup=main.reply_markup,
    )


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
    now: datetime | None = None,
) -> BotProfileReply:
    """Render full details for one client-owned appointment."""

    appointment = _appointment_for_client(
        partner=partner,
        client_profile=client_profile,
        appointment_id=appointment_id,
    )

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
    rows = []
    pending_proposal = _pending_proposal_for_appointment(
        appointment=appointment,
        now=now or timezone.now(),
    )
    if pending_proposal is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    text=_label("review_proposal", language),
                    callback_data=_callback(
                        "proposal",
                        pending_proposal.id.hex,
                        return_action,
                        return_page,
                    ),
                )
            ]
        )
    if appointment.status in {
        Appointment.Status.REQUESTED,
        Appointment.Status.CONFIRMED,
    } and appointment.start_at > (now or timezone.now()):
        rows.append(
            [
                InlineKeyboardButton(
                    text=_label("cancel_appointment", language),
                    callback_data=_callback(
                        "cancel",
                        appointment.id.hex,
                        return_action,
                        return_page,
                    ),
                )
            ]
        )
    rows.append([_back_button(_callback(return_action, return_page), language)])
    return BotProfileReply(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


def build_appointment_proposal_reply(
    *,
    partner: Partner,
    client_profile: ClientProfile,
    proposal_id: UUID,
    return_action: str = "future",
    return_page: int = 1,
    now: datetime | None = None,
) -> BotProfileReply:
    """Render one live proposal and its selectable options."""

    proposal = _proposal_for_client(
        partner=partner,
        client_profile=client_profile,
        proposal_id=proposal_id,
        now=now or timezone.now(),
    )
    options = tuple(proposal.options.order_by("sort_order", "id"))
    if not options:
        raise ValidationError({"proposal": "Proposal has no options."})

    language = client_profile.language
    first_start = _proposal_option_start(proposal=proposal, option=options[0])
    first_local = to_partner_time(first_start, partner.timezone)
    expiry_local = to_partner_time(proposal.expires_at, partner.timezone)
    text = _render(
        partner=partner,
        key="appointment.reschedule_proposed",
        language=language,
        context={
            "partner_name": partner.name,
            "date": first_local.date().isoformat(),
            "time": first_local.strftime("%H:%M"),
            "services": _appointment_services_label(proposal.appointment),
            "proposal_expires_at": expiry_local.strftime("%Y-%m-%d %H:%M"),
        },
    )
    option_lines = "\n".join(
        f"{index}. {_proposal_option_label(partner=partner, proposal=proposal, option=option)}"
        for index, option in enumerate(options, start=1)
    )
    text = f"{text}\n\n{option_lines}"
    if proposal.message.strip():
        text = f"{text}\n\n{_label('master_message', language)}: {proposal.message.strip()}"

    rows = [
        [
            InlineKeyboardButton(
                text=f"{index}. {_label('accept_time', language)}",
                callback_data=_callback(
                    "proposalgo",
                    option.id,
                    return_action,
                    return_page,
                ),
            )
        ]
        for index, option in enumerate(options, start=1)
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text=_label("decline_proposal", language),
                callback_data=_callback(
                    "proposalno",
                    proposal.id.hex,
                    return_action,
                    return_page,
                ),
            )
        ]
    )
    rows.append(
        [
            _back_button(
                _callback(
                    "details",
                    proposal.appointment_id.hex,
                    return_action,
                    return_page,
                ),
                language,
            )
        ]
    )
    return BotProfileReply(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


def accept_appointment_proposal_reply(
    *,
    partner: Partner,
    client_profile: ClientProfile,
    option_id: int,
    return_action: str = "future",
    return_page: int = 1,
    now: datetime | None = None,
) -> BotProfileReply:
    """Accept one client-owned option through the concurrency-safe owner service."""

    option = _proposal_option_for_client(
        partner=partner,
        client_profile=client_profile,
        option_id=option_id,
    )
    appointment = accept_appointment_proposal_option(
        client_profile=client_profile,
        option=option,
        now=now,
    )
    language = client_profile.language
    partner_time = to_partner_time(appointment.start_at, partner.timezone)
    text = _render(
        partner=partner,
        key="appointment.reschedule_proposal_accepted",
        language=language,
        context={
            "new_date": partner_time.date().isoformat(),
            "new_time": partner_time.strftime("%H:%M"),
            "services": _appointment_services_label(appointment),
        },
    )
    return BotProfileReply(
        text=text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    _back_button(
                        _callback(
                            "details",
                            appointment.id.hex,
                            return_action,
                            return_page,
                        ),
                        language,
                    )
                ]
            ]
        ),
    )


def decline_appointment_proposal_reply(
    *,
    partner: Partner,
    client_profile: ClientProfile,
    proposal_id: UUID,
    return_action: str = "future",
    return_page: int = 1,
    now: datetime | None = None,
) -> BotProfileReply:
    """Decline one live client-owned proposal without changing the appointment."""

    proposal = _proposal_for_client(
        partner=partner,
        client_profile=client_profile,
        proposal_id=proposal_id,
        now=now or timezone.now(),
    )
    declined = decline_appointment_proposal(
        client_profile=client_profile,
        proposal=proposal,
        now=now,
    )
    language = client_profile.language
    partner_time = to_partner_time(declined.appointment.start_at, partner.timezone)
    text = _render(
        partner=partner,
        key="appointment.reschedule_proposal_declined",
        language=language,
        context={
            "date": partner_time.date().isoformat(),
            "time": partner_time.strftime("%H:%M"),
            "services": _appointment_services_label(declined.appointment),
        },
    )
    return BotProfileReply(
        text=text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    _back_button(
                        _callback(
                            "details",
                            declined.appointment_id.hex,
                            return_action,
                            return_page,
                        ),
                        language,
                    )
                ]
            ]
        ),
    )


def build_cancel_appointment_reply(
    *,
    partner: Partner,
    client_profile: ClientProfile,
    appointment_id: UUID,
    return_action: str = "future",
    return_page: int = 1,
    now: datetime | None = None,
) -> BotProfileReply:
    """Render a fresh cancellation confirmation or policy block."""

    appointment = _appointment_for_client(
        partner=partner,
        client_profile=client_profile,
        appointment_id=appointment_id,
    )
    current_time = now or timezone.now()
    policy = client_cancellation_policy(
        client_profile=client_profile,
        appointment=appointment,
        now=current_time,
    )
    language = client_profile.language
    back_callback = _callback("details", appointment.id.hex, return_action, return_page)
    if not policy.allowed:
        if policy.reason_code == "late_cancellation_blocked":
            return BotProfileReply(
                text=_render(
                    partner=partner,
                    key="appointment.late_cancel_blocked",
                    language=language,
                ),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=_label("contact_master", language),
                                callback_data=_callback("contact"),
                            )
                        ],
                        [_back_button(back_callback, language)],
                    ],
                ),
            )
        raise ValidationError({"appointment": "This appointment cannot be cancelled."})

    text = _render(
        partner=partner,
        key="appointment.cancel_confirm_prompt",
        language=language,
        context=_appointment_action_context(partner=partner, appointment=appointment),
    )
    penalty_acknowledged = "0"
    if policy.requires_penalty_confirmation:
        warning = _render(
            partner=partner,
            key="appointment.late_cancel_penalty_warning",
            language=language,
            context={
                "penalty_amount": _money_label(policy.penalty_amount),
                "currency": policy.currency,
                "deadline_hours": str(policy.deadline_hours),
            },
        )
        text = f"{text}\n\n{warning}"
        penalty_acknowledged = "1"

    rows = [
        [
            InlineKeyboardButton(
                text=_label("confirm_cancel", language),
                callback_data=_callback(
                    "cancelgo",
                    appointment.id.hex,
                    return_action,
                    return_page,
                    penalty_acknowledged,
                ),
            )
        ],
        [_back_button(back_callback, language)],
    ]
    return BotProfileReply(text=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


def confirm_cancel_appointment_reply(
    *,
    partner: Partner,
    client_profile: ClientProfile,
    appointment_id: UUID,
    accept_late_penalty: bool,
    return_action: str = "future",
    return_page: int = 1,
    now: datetime | None = None,
) -> BotProfileReply:
    """Persist a cancellation after rechecking policy at confirmation time."""

    appointment = _appointment_for_client(
        partner=partner,
        client_profile=client_profile,
        appointment_id=appointment_id,
    )
    current_time = now or timezone.now()
    policy = client_cancellation_policy(
        client_profile=client_profile,
        appointment=appointment,
        now=current_time,
    )
    if not policy.allowed or (policy.requires_penalty_confirmation and not accept_late_penalty):
        return build_cancel_appointment_reply(
            partner=partner,
            client_profile=client_profile,
            appointment_id=appointment.id,
            return_action=return_action,
            return_page=return_page,
            now=current_time,
        )

    result = cancel_appointment_by_client(
        client_profile=client_profile,
        appointment=appointment,
        accept_late_penalty=accept_late_penalty,
        now=current_time,
    )
    language = client_profile.language
    if result.penalty is not None:
        key = "appointment.late_cancelled_with_penalty"
        context = {
            "penalty_amount": _money_label(result.penalty.amount),
            "currency": result.penalty.currency,
        }
    elif appointment.status == Appointment.Status.REQUESTED:
        key = "booking.request_cancelled_by_client"
        context = _appointment_action_context(partner=partner, appointment=appointment)
    else:
        key = "appointment.cancelled_by_client"
        context = _appointment_action_context(partner=partner, appointment=appointment)

    return BotProfileReply(
        text=_render(
            partner=partner,
            key=key,
            language=language,
            context=context,
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [_back_button(_callback(return_action, return_page), language)],
            ],
        ),
    )


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


def parse_integer_token(value: str) -> int:
    """Parse a positive integer model identifier from callback data."""

    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"callback": "Invalid callback identifier."}) from exc
    if parsed < 1:
        raise ValidationError({"callback": "Invalid callback identifier."})
    return parsed


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


def _appointment_for_client(
    *,
    partner: Partner,
    client_profile: ClientProfile,
    appointment_id: UUID,
) -> Appointment:
    try:
        return (
            Appointment.objects.filter(
                partner=partner,
                client_profile=client_profile,
                id=appointment_id,
            )
            .select_related("partner", "client_profile")
            .prefetch_related("service_items")
            .get()
        )
    except Appointment.DoesNotExist as exc:
        raise ValidationError({"appointment": "Appointment is not available."}) from exc


def _pending_proposal_for_appointment(
    *,
    appointment: Appointment,
    now: datetime,
) -> AppointmentProposal | None:
    return (
        AppointmentProposal.objects.filter(
            partner=appointment.partner,
            appointment=appointment,
            status=AppointmentProposal.Status.PENDING,
            expires_at__gt=now,
        )
        .order_by("-created_at")
        .first()
    )


def _proposal_for_client(
    *,
    partner: Partner,
    client_profile: ClientProfile,
    proposal_id: UUID,
    now: datetime,
) -> AppointmentProposal:
    try:
        proposal = (
            AppointmentProposal.objects.filter(
                partner=partner,
                appointment__client_profile=client_profile,
                id=proposal_id,
            )
            .select_related("partner", "appointment", "appointment__client_profile")
            .prefetch_related("options", "appointment__service_items")
            .get()
        )
    except AppointmentProposal.DoesNotExist as exc:
        raise ValidationError({"proposal": "Proposal is not available."}) from exc
    if proposal.status != AppointmentProposal.Status.PENDING or proposal.expires_at <= now:
        raise ValidationError({"proposal": "Proposal is no longer available."})
    return proposal


def _proposal_option_for_client(
    *,
    partner: Partner,
    client_profile: ClientProfile,
    option_id: int,
) -> AppointmentProposalOption:
    try:
        return (
            AppointmentProposalOption.objects.filter(
                id=option_id,
                proposal__partner=partner,
                proposal__appointment__client_profile=client_profile,
            )
            .select_related(
                "proposal",
                "proposal__partner",
                "proposal__appointment",
                "proposal__appointment__client_profile",
            )
            .get()
        )
    except AppointmentProposalOption.DoesNotExist as exc:
        raise ValidationError({"proposal": "Proposal option is not available."}) from exc


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


def _appointment_action_context(
    *,
    partner: Partner,
    appointment: Appointment,
) -> dict[str, str]:
    partner_time = to_partner_time(appointment.start_at, partner.timezone)
    return {
        "date": partner_time.date().isoformat(),
        "time": partner_time.strftime("%H:%M"),
        "services": _appointment_services_label(appointment),
    }


def _appointment_services_label(appointment: Appointment) -> str:
    return ", ".join(item.service_name_snapshot for item in appointment.service_items.all())


def _proposal_option_start(
    *,
    proposal: AppointmentProposal,
    option: AppointmentProposalOption,
) -> datetime:
    return option.start_at or proposal.appointment.start_at


def _proposal_option_label(
    *,
    partner: Partner,
    proposal: AppointmentProposal,
    option: AppointmentProposalOption,
) -> str:
    local_start = to_partner_time(
        _proposal_option_start(proposal=proposal, option=option),
        partner.timezone,
    )
    return local_start.strftime("%Y-%m-%d %H:%M")


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
        "cancel_appointment": {
            "uk": "Скасувати запис",
            "ru": "Отменить запись",
            "en": "Cancel booking",
        },
        "contact_master": {
            "uk": "Написати майстру",
            "ru": "Написать мастеру",
            "en": "Message master",
        },
        "comment": {"uk": "Коментар", "ru": "Комментарий", "en": "Comment"},
        "confirm_cancel": {
            "uk": "Так, скасувати",
            "ru": "Да, отменить",
            "en": "Yes, cancel",
        },
        "details": {"uk": "Деталі", "ru": "Детали", "en": "Details"},
        "future": {"uk": "Мої записи", "ru": "Мои записи", "en": "My bookings"},
        "history": {"uk": "Історія", "ru": "История", "en": "History"},
        "language": {"uk": "Мова", "ru": "Язык", "en": "Language"},
        "not_set": {"uk": "не вказано", "ru": "не указано", "en": "not set"},
        "reason": {"uk": "Причина", "ru": "Причина", "en": "Reason"},
        "accept_time": {
            "uk": "Обрати цей час",
            "ru": "Выбрать это время",
            "en": "Choose this time",
        },
        "decline_proposal": {
            "uk": "Відхилити пропозицію",
            "ru": "Отклонить предложение",
            "en": "Decline proposal",
        },
        "master_message": {
            "uk": "Повідомлення майстра",
            "ru": "Сообщение мастера",
            "en": "Master message",
        },
        "review_proposal": {
            "uk": "Переглянути запропонований час",
            "ru": "Посмотреть предложенное время",
            "en": "Review proposed time",
        },
    }
    translations = labels[key]
    return translations.get(language, translations["uk"])
