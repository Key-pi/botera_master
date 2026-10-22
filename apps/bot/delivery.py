from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from datetime import timezone as datetime_timezone

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from asgiref.sync import async_to_sync
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.appointments.models import Appointment, AppointmentProposal, AppointmentProposalOption
from apps.bot.master import (
    build_master_booking_request_reply,
    master_booking_request_markup,
)
from apps.bot.messaging import master_client_message_markup
from apps.bot.notification_failures import (
    business_delivery_failure_reason,
    failed_delivery_to_master_markup,
)
from apps.bot.selectors import primary_master_telegram_contact
from apps.bot.services import (
    decrypt_bot_token,
    mark_master_contact_delivery_failure,
    mark_master_contact_reachable,
    render_content_template,
)
from apps.broadcasts.models import BroadcastRecipient
from apps.broadcasts.services import BroadcastDeliveryResult
from apps.finance.models import Penalty
from apps.messaging.models import ConversationMessage
from apps.notifications.models import ScheduledNotification
from apps.notifications.services import DeliveryResult
from core.money import Money
from core.security.masking import mask_sensitive_text
from core.timezones import get_zone, to_partner_time

TelegramSender = Callable[
    [str, int, str, str, InlineKeyboardMarkup | None],
    Awaitable[None],
]


@dataclass(frozen=True, slots=True)
class TelegramPayload:
    token: str
    chat_id: int
    text: str
    photo: str = ""
    reply_markup: InlineKeyboardMarkup | None = None


def deliver_scheduled_notification(notification: ScheduledNotification) -> DeliveryResult:
    """Deliver one scheduled notification through the partner bot."""

    payload = _payload_for_notification(notification)
    if payload is None:
        return DeliveryResult(success=False, skipped=True)
    result = _send_payload(payload)
    if result.success and _is_master_notification(notification):
        contact = primary_master_telegram_contact(notification.partner)
        if contact is not None:
            mark_master_contact_reachable(contact=contact)
    if result.terminal and _is_master_notification(notification):
        contact = primary_master_telegram_contact(notification.partner)
        if contact is not None:
            mark_master_contact_delivery_failure(
                contact=contact,
                failure_code=result.failure_code or "terminal_delivery_failure",
            )
    return result


def deliver_broadcast_recipient(recipient: BroadcastRecipient) -> BroadcastDeliveryResult:
    """Deliver one broadcast recipient through the partner bot."""

    if not recipient.client_profile.telegram_identity_id:
        return BroadcastDeliveryResult(success=False, skipped=True)
    token = _token_for_partner(recipient.partner)
    payload = TelegramPayload(
        token=token,
        chat_id=recipient.client_profile.telegram_identity.telegram_user_id,
        text=recipient.campaign.text,
        photo=recipient.campaign.photo,
        reply_markup=_broadcast_reply_markup(recipient.campaign.buttons),
    )
    result = _send_payload(payload)
    return BroadcastDeliveryResult(
        success=result.success,
        failure_code=result.failure_code,
        failure_message=result.failure_message,
        terminal=result.terminal,
        skipped=result.skipped,
        retry_after_seconds=result.retry_after_seconds,
    )


def deliver_conversation_message(message: ConversationMessage) -> DeliveryResult:
    """Deliver one queued client-master conversation message through the partner bot."""

    payload = _payload_for_conversation_message(message)
    if payload is None:
        return DeliveryResult(success=False, skipped=True)
    result = _send_payload(payload)
    if message.direction == ConversationMessage.Direction.CLIENT_TO_MASTER:
        contact = primary_master_telegram_contact(message.partner)
        if contact is not None and result.success:
            mark_master_contact_reachable(contact=contact)
        if contact is not None and result.terminal:
            mark_master_contact_delivery_failure(
                contact=contact,
                failure_code=result.failure_code or "terminal_delivery_failure",
            )
    return result


def _payload_for_notification(notification: ScheduledNotification) -> TelegramPayload | None:
    token = _token_for_partner(notification.partner)
    if notification.type == ScheduledNotification.Type.CLIENT_APPOINTMENT_CREATED:
        if (
            notification.client_profile is None
            or not notification.client_profile.telegram_identity_id
            or notification.appointment is None
        ):
            return None
        template = render_content_template(
            partner=notification.partner,
            key="booking.confirmed_created",
            language=notification.client_profile.language,
            context=_appointment_template_context(notification.appointment),
        )
        return TelegramPayload(
            token=token,
            chat_id=notification.client_profile.telegram_identity.telegram_user_id,
            text=template.body,
        )

    if notification.type in {
        ScheduledNotification.Type.CLIENT_BOOKING_REQUEST_CONFIRMED,
        ScheduledNotification.Type.CLIENT_BOOKING_REQUEST_DECLINED,
    }:
        if (
            notification.client_profile is None
            or not notification.client_profile.telegram_identity_id
            or notification.appointment is None
        ):
            return None
        key = (
            "booking.request_confirmed_by_master"
            if notification.type == ScheduledNotification.Type.CLIENT_BOOKING_REQUEST_CONFIRMED
            else "booking.request_declined_by_master"
        )
        template = render_content_template(
            partner=notification.partner,
            key=key,
            language=notification.client_profile.language,
            context=_appointment_template_context(notification.appointment),
        )
        return TelegramPayload(
            token=token,
            chat_id=notification.client_profile.telegram_identity.telegram_user_id,
            text=template.body,
        )

    client_status_key_by_type = {
        ScheduledNotification.Type.CLIENT_APPOINTMENT_CANCELLED_BY_MASTER: (
            "appointment.cancelled_by_master"
        ),
        ScheduledNotification.Type.CLIENT_APPOINTMENT_COMPLETED: "appointment.completed",
        ScheduledNotification.Type.CLIENT_APPOINTMENT_NO_SHOW: "appointment.no_show",
    }
    client_status_key = client_status_key_by_type.get(notification.type)
    if client_status_key is not None:
        if (
            notification.client_profile is None
            or not notification.client_profile.telegram_identity_id
            or notification.appointment is None
        ):
            return None
        template = render_content_template(
            partner=notification.partner,
            key=client_status_key,
            language=notification.client_profile.language,
            context=_appointment_template_context(notification.appointment),
        )
        return TelegramPayload(
            token=token,
            chat_id=notification.client_profile.telegram_identity.telegram_user_id,
            text=template.body,
        )

    penalty_key_by_type = {
        ScheduledNotification.Type.CLIENT_PENALTY_PAID: "penalty.paid",
        ScheduledNotification.Type.CLIENT_PENALTY_WAIVED: "penalty.waived",
    }
    if notification.type == ScheduledNotification.Type.CLIENT_PENALTY_CREATED:
        penalty_key_by_reason = {
            Penalty.Reason.LATE_CANCEL: "penalty.created_late_cancel",
            Penalty.Reason.NO_SHOW: "penalty.created_no_show",
            Penalty.Reason.LATE_ARRIVAL: "penalty.created_late_arrival",
        }
        penalty_key = (
            penalty_key_by_reason.get(notification.penalty.reason)
            if notification.penalty is not None
            else None
        )
    else:
        penalty_key = penalty_key_by_type.get(notification.type)
    if penalty_key is not None:
        if (
            notification.penalty is None
            or notification.client_profile is None
            or not notification.client_profile.telegram_identity_id
        ):
            return None
        template = render_content_template(
            partner=notification.partner,
            key=penalty_key,
            language=notification.client_profile.language,
            context=_penalty_template_context(notification.penalty),
        )
        return TelegramPayload(
            token=token,
            chat_id=notification.client_profile.telegram_identity.telegram_user_id,
            text=template.body,
        )

    if notification.type == ScheduledNotification.Type.MASTER_BOOKING_REQUEST:
        if notification.appointment is None:
            return None
        contact = primary_master_telegram_contact(notification.partner)
        if contact is None:
            return None
        try:
            reply = build_master_booking_request_reply(
                partner=notification.partner,
                appointment_id=notification.appointment.id,
            )
        except ValidationError:
            # Appointment state may change after dispatch preparation but before
            # Telegram I/O. Treat the now-obsolete request as a skipped send.
            return None
        return TelegramPayload(
            token=token,
            chat_id=contact.telegram_identity.telegram_user_id,
            text=reply.text,
            reply_markup=reply.reply_markup,
        )

    if notification.type == ScheduledNotification.Type.MASTER_CLIENT_BOOKED_APPOINTMENT:
        if notification.appointment is None:
            return None
        context = _appointment_template_context(notification.appointment)
        context["comment"] = _appointment_comment_block(notification.appointment)
        return _master_notification_payload(
            token=token,
            notification=notification,
            key="master.client_booked_appointment",
            context=context,
        )

    if notification.type == ScheduledNotification.Type.CLIENT_APPOINTMENT_REMINDER:
        if (
            notification.client_profile is None
            or not notification.client_profile.telegram_identity_id
            or notification.appointment is None
        ):
            return None
        template = render_content_template(
            partner=notification.partner,
            key="reminder.client_before_appointment",
            language=notification.client_profile.language,
            context=_appointment_template_context(notification.appointment),
        )
        return TelegramPayload(
            token=token,
            chat_id=notification.client_profile.telegram_identity.telegram_user_id,
            text=template.body,
        )

    if notification.type == ScheduledNotification.Type.FAILED_DELIVERY_TO_MASTER:
        source = notification.source_notification
        if (
            source is None
            or source.status != ScheduledNotification.Status.FAILED
            or not source.failed_visible_to_partner
            or notification.appointment is None
        ):
            return None
        contact = primary_master_telegram_contact(notification.partner)
        if contact is None:
            return None
        context = _appointment_template_context(notification.appointment) | {
            "failure_reason": business_delivery_failure_reason(
                failure_code=source.last_failure_code,
                language=notification.partner.default_language,
            )
        }
        template = render_content_template(
            partner=notification.partner,
            key="reminder.failed_delivery_to_master",
            language=notification.partner.default_language,
            context=context,
        )
        return TelegramPayload(
            token=token,
            chat_id=contact.telegram_identity.telegram_user_id,
            text=template.body,
            reply_markup=(
                failed_delivery_to_master_markup(
                    source_notification_id=source.id,
                    language=notification.partner.default_language,
                )
                if contact.user_id
                else None
            ),
        )

    if notification.type == ScheduledNotification.Type.MASTER_APPOINTMENT_REMINDER:
        if notification.appointment is None:
            return None
        return _master_notification_payload(
            token=token,
            notification=notification,
            key="reminder.master_before_appointment",
            context=_appointment_template_context(notification.appointment),
        )

    if notification.type == ScheduledNotification.Type.MASTER_CLIENT_CANCELLED_APPOINTMENT:
        if notification.appointment is None:
            return None
        return _master_notification_payload(
            token=token,
            notification=notification,
            key="master.client_cancelled_appointment",
            context=_appointment_template_context(notification.appointment),
        )

    if notification.type == ScheduledNotification.Type.MASTER_CLIENT_REQUESTED_RESCHEDULE:
        if notification.appointment is None:
            return None
        proposal = notification.appointment_proposal
        option = None
        if proposal is not None:
            option = proposal.options.order_by("sort_order", "id").first()
        context = (
            _proposal_template_context(proposal=proposal, option=option)
            if proposal is not None
            else _appointment_template_context(notification.appointment)
        )
        return _master_notification_payload(
            token=token,
            notification=notification,
            key="master.client_requested_reschedule",
            context=context,
        )

    if notification.type == ScheduledNotification.Type.CLIENT_APPOINTMENT_PROPOSAL:
        proposal = notification.appointment_proposal
        if (
            proposal is None
            or notification.client_profile is None
            or not notification.client_profile.telegram_identity_id
        ):
            return None
        options = tuple(proposal.options.order_by("sort_order", "id"))
        if not options:
            return None
        template = render_content_template(
            partner=notification.partner,
            key="appointment.reschedule_proposed",
            language=notification.client_profile.language,
            context=_proposal_template_context(proposal=proposal, option=options[0]),
        )
        text = template.body
        if proposal.message.strip():
            text = f"{text}\n\n{proposal.message.strip()}"
        return TelegramPayload(
            token=token,
            chat_id=notification.client_profile.telegram_identity.telegram_user_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=_proposal_review_label(notification.client_profile.language),
                            callback_data=f"pf:proposal:{proposal.id.hex}:future:1",
                        )
                    ]
                ]
            ),
        )

    if notification.type == ScheduledNotification.Type.CLIENT_APPOINTMENT_PROPOSAL_EXPIRED:
        proposal = notification.appointment_proposal
        if (
            proposal is None
            or notification.client_profile is None
            or not notification.client_profile.telegram_identity_id
        ):
            return None
        template = render_content_template(
            partner=notification.partner,
            key="appointment.reschedule_proposal_expired",
            language=notification.client_profile.language,
            context=_proposal_template_context(proposal=proposal),
        )
        return TelegramPayload(
            token=token,
            chat_id=notification.client_profile.telegram_identity.telegram_user_id,
            text=template.body,
        )

    if notification.type in {
        ScheduledNotification.Type.CLIENT_APPOINTMENT_PROPOSAL_ACCEPTED,
        ScheduledNotification.Type.CLIENT_APPOINTMENT_PROPOSAL_DECLINED,
    }:
        proposal = notification.appointment_proposal
        if (
            proposal is None
            or notification.client_profile is None
            or not notification.client_profile.telegram_identity_id
        ):
            return None
        option = proposal.selected_option or proposal.options.order_by("sort_order", "id").first()
        key = (
            "appointment.reschedule_proposal_accepted"
            if notification.type == ScheduledNotification.Type.CLIENT_APPOINTMENT_PROPOSAL_ACCEPTED
            else "appointment.reschedule_proposal_declined"
        )
        template = render_content_template(
            partner=notification.partner,
            key=key,
            language=notification.client_profile.language,
            context=_proposal_template_context(proposal=proposal, option=option),
        )
        return TelegramPayload(
            token=token,
            chat_id=notification.client_profile.telegram_identity.telegram_user_id,
            text=template.body,
        )

    if notification.type == ScheduledNotification.Type.MASTER_CLIENT_ACCEPTED_PROPOSAL:
        proposal = notification.appointment_proposal
        if proposal is None:
            return None
        return _master_notification_payload(
            token=token,
            notification=notification,
            key="master.client_accepted_reschedule",
            context=_proposal_template_context(
                proposal=proposal,
                option=proposal.selected_option,
            ),
        )

    if notification.type == ScheduledNotification.Type.MASTER_CLIENT_DECLINED_PROPOSAL:
        proposal = notification.appointment_proposal
        if proposal is None:
            return None
        return _master_notification_payload(
            token=token,
            notification=notification,
            key="master.client_declined_reschedule",
            context=_proposal_template_context(proposal=proposal),
        )

    if notification.type == ScheduledNotification.Type.MASTER_APPOINTMENT_PROPOSAL_EXPIRED:
        proposal = notification.appointment_proposal
        if proposal is None:
            return None
        return _master_notification_payload(
            token=token,
            notification=notification,
            key="master.appointment_proposal_expired",
            context=_proposal_template_context(proposal=proposal),
        )

    if notification.type == ScheduledNotification.Type.STALE_REQUESTED:
        if notification.appointment is None:
            return None
        return _master_notification_payload(
            token=token,
            notification=notification,
            key="reminder.stale_requested",
            context=_appointment_template_context(notification.appointment)
            | {"request_age": _request_age_label(notification.appointment)},
            reply_markup=master_booking_request_markup(
                appointment_id=notification.appointment.id,
                language=notification.partner.default_language,
            ),
        )

    if notification.type == ScheduledNotification.Type.MASTER_MORNING_DIGEST:
        digest_date = _digest_local_date(notification)
        appointments = _appointments_for_digest(notification.partner, digest_date)
        key = (
            "reminder.master_morning_digest"
            if appointments
            else "reminder.master_morning_digest_empty"
        )
        context = {
            "date": digest_date.isoformat(),
            "appointments": "\n".join(_digest_line(item) for item in appointments),
        }
        return _master_notification_payload(
            token=token,
            notification=notification,
            key=key,
            context=context,
        )

    return None


def _payload_for_conversation_message(message: ConversationMessage) -> TelegramPayload | None:
    token = _token_for_partner(message.partner)
    if message.direction == ConversationMessage.Direction.MASTER_TO_CLIENT:
        if not message.client_profile.telegram_identity_id:
            return None
        template = render_content_template(
            partner=message.partner,
            key="messaging.master_reply_received_for_client",
            language=message.client_profile.language,
            context={
                "partner_name": message.partner.name,
                "message_text": message.text,
            },
        )
        return TelegramPayload(
            token=token,
            chat_id=message.client_profile.telegram_identity.telegram_user_id,
            text=template.body,
        )

    contact = primary_master_telegram_contact(message.partner)
    if contact is None:
        return None
    template = render_content_template(
        partner=message.partner,
        key="messaging.client_message_received_for_master",
        language=message.partner.default_language,
        context={
            "client_name": message.client_profile.display_name,
            "client_phone": message.client_profile.phone,
            "message_preview": message.text,
        },
    )
    return TelegramPayload(
        token=token,
        chat_id=contact.telegram_identity.telegram_user_id,
        text=template.body,
        reply_markup=(
            master_client_message_markup(
                message_id=message.id,
                language=message.partner.default_language,
            )
            if contact.user_id
            else None
        ),
    )


def _master_notification_payload(
    *,
    token: str,
    notification: ScheduledNotification,
    key: str,
    context: dict,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> TelegramPayload | None:
    contact = primary_master_telegram_contact(notification.partner)
    if contact is None:
        return None
    template = render_content_template(
        partner=notification.partner,
        key=key,
        language=notification.partner.default_language,
        context=context,
    )
    return TelegramPayload(
        token=token,
        chat_id=contact.telegram_identity.telegram_user_id,
        text=template.body,
        reply_markup=reply_markup,
    )


def _token_for_partner(partner) -> str:
    return decrypt_bot_token(partner.bot_instance)


def _send_payload(payload: TelegramPayload) -> DeliveryResult:
    try:
        async_to_sync(_send_telegram_payload)(
            payload.token,
            payload.chat_id,
            payload.text,
            payload.photo,
            payload.reply_markup,
        )
    except TelegramRetryAfter as exc:
        return DeliveryResult(
            success=False,
            failure_code="telegram_429",
            failure_message=mask_sensitive_text(str(exc)),
            retry_after_seconds=exc.retry_after,
        )
    except TelegramForbiddenError as exc:
        return DeliveryResult(
            success=False,
            failure_code="bot_blocked",
            failure_message=mask_sensitive_text(str(exc)),
            terminal=True,
        )
    except TelegramNotFound as exc:
        return DeliveryResult(
            success=False,
            failure_code="chat_not_found",
            failure_message=mask_sensitive_text(str(exc)),
            terminal=True,
        )
    except TelegramBadRequest as exc:
        code = _bad_request_failure_code(exc)
        return DeliveryResult(
            success=False,
            failure_code=code,
            failure_message=mask_sensitive_text(str(exc)),
            terminal=code == "chat_not_found",
        )
    except TelegramUnauthorizedError as exc:
        return DeliveryResult(
            success=False,
            failure_code="bot_unauthorized",
            failure_message=mask_sensitive_text(str(exc)),
        )
    except TelegramNetworkError as exc:
        return DeliveryResult(
            success=False,
            failure_code="telegram_network_error",
            failure_message=mask_sensitive_text(str(exc)),
        )
    except TelegramAPIError as exc:
        return DeliveryResult(
            success=False,
            failure_code="telegram_api_error",
            failure_message=mask_sensitive_text(str(exc)),
        )
    return DeliveryResult(success=True)


async def _send_telegram_payload(
    token: str,
    chat_id: int,
    text: str,
    photo: str = "",
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    bot = Bot(token=token)
    try:
        if photo:
            await bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=text or None,
                reply_markup=reply_markup,
            )
            return
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
    finally:
        await bot.session.close()


def _appointment_template_context(appointment: Appointment) -> dict[str, str]:
    partner_time = to_partner_time(appointment.start_at, appointment.partner.timezone)
    return {
        "partner_name": appointment.partner.name,
        "client_name": appointment.client_profile.display_name,
        "client_phone": appointment.client_profile.phone,
        "date": partner_time.date().isoformat(),
        "time": _format_local_time(partner_time),
        "services": _appointment_services_label(appointment),
        "reason": appointment.client_visible_reason.strip(),
    }


def _penalty_template_context(penalty: Penalty) -> dict[str, str]:
    context = {
        "penalty_amount": str(
            Money(amount=penalty.amount, currency=penalty.currency).to_major_decimal()
        ),
        "currency": penalty.currency,
        "late_minutes": str(penalty.late_by_minutes or ""),
        "appointment_date": "",
        "appointment_time": "",
    }
    if penalty.source_appointment is not None:
        partner_time = to_partner_time(
            penalty.source_appointment.start_at,
            penalty.partner.timezone,
        )
        context["appointment_date"] = partner_time.date().isoformat()
        context["appointment_time"] = _format_local_time(partner_time)
    return context


def _appointment_services_label(appointment: Appointment) -> str:
    items = list(appointment.service_items.all())
    if not items:
        return ""
    return ", ".join(item.service_name_snapshot for item in items)


def _appointment_comment_block(appointment: Appointment) -> str:
    comment = appointment.client_comment.strip()
    if not comment:
        return ""
    labels = {
        "uk": "Коментар",
        "ru": "Комментарий",
        "en": "Comment",
    }
    label = labels.get(appointment.partner.default_language, labels["uk"])
    return f"\n{label}: {comment}"


def _proposal_template_context(
    *,
    proposal: AppointmentProposal,
    option: AppointmentProposalOption | None = None,
) -> dict[str, str]:
    appointment = proposal.appointment
    effective_option = option or proposal.selected_option
    start_at = (
        effective_option.start_at
        if effective_option is not None and effective_option.start_at is not None
        else appointment.start_at
    )
    partner_time = to_partner_time(start_at, proposal.partner.timezone)
    expiry_time = to_partner_time(proposal.expires_at, proposal.partner.timezone)
    return {
        "partner_name": proposal.partner.name,
        "client_name": appointment.client_profile.display_name,
        "date": partner_time.date().isoformat(),
        "time": _format_local_time(partner_time),
        "new_date": partner_time.date().isoformat(),
        "new_time": _format_local_time(partner_time),
        "services": _appointment_services_label(appointment),
        "proposal_expires_at": expiry_time.strftime("%Y-%m-%d %H:%M"),
    }


def _proposal_review_label(language: str) -> str:
    labels = {
        "uk": "Переглянути варіанти",
        "ru": "Посмотреть варианты",
        "en": "Review options",
    }
    return labels.get(language, labels["uk"])


def _request_age_label(appointment: Appointment) -> str:
    anchor = appointment.requested_at or appointment.created_at
    minutes = max(int((timezone.now() - anchor).total_seconds() // 60), 0)
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    return f"{hours} h"


def _digest_local_date(notification: ScheduledNotification):
    partner_zone = get_zone(notification.partner.timezone)
    return notification.due_at.astimezone(partner_zone).date()


def _appointments_for_digest(partner, local_date) -> tuple[Appointment, ...]:
    partner_zone = get_zone(partner.timezone)
    local_start = datetime.combine(local_date, time.min, tzinfo=partner_zone)
    local_end = local_start + timedelta(days=1)
    return tuple(
        Appointment.objects.filter(
            partner=partner,
            status__in=[Appointment.Status.REQUESTED, Appointment.Status.CONFIRMED],
            start_at__gte=local_start.astimezone(datetime_timezone.utc),
            start_at__lt=local_end.astimezone(datetime_timezone.utc),
        )
        .select_related("client_profile", "partner")
        .prefetch_related("service_items")
        .order_by("start_at")
    )


def _digest_line(appointment: Appointment) -> str:
    partner_time = to_partner_time(appointment.start_at, appointment.partner.timezone)
    return (
        f"{_format_local_time(partner_time)} - "
        f"{appointment.client_profile.display_name}: "
        f"{_appointment_services_label(appointment)}"
    )


def _format_local_time(value: datetime) -> str:
    return value.strftime("%H:%M")


def _broadcast_reply_markup(buttons: list[dict]) -> InlineKeyboardMarkup | None:
    rows: list[list[InlineKeyboardButton]] = []
    for button in buttons:
        label = str(button.get("label", "")).strip()
        button_type = str(button.get("type", "")).strip()
        if not label:
            continue
        if button_type == "external_link":
            url = str(button.get("url", "")).strip()
            if url:
                rows.append([InlineKeyboardButton(text=label, url=url)])
            continue
        if button_type in {"book", "my_appointments"}:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=label,
                        callback_data=f"broadcast:{button_type}",
                    )
                ]
            )
    if not rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _bad_request_failure_code(exc: TelegramBadRequest) -> str:
    message = str(exc).lower()
    if "chat not found" in message:
        return "chat_not_found"
    if "bot was blocked" in message or "user is deactivated" in message:
        return "bot_blocked"
    return "telegram_bad_request"


def _is_master_notification(notification: ScheduledNotification) -> bool:
    return notification.type in {
        ScheduledNotification.Type.MASTER_BOOKING_REQUEST,
        ScheduledNotification.Type.MASTER_CLIENT_BOOKED_APPOINTMENT,
        ScheduledNotification.Type.MASTER_APPOINTMENT_REMINDER,
        ScheduledNotification.Type.MASTER_CLIENT_CANCELLED_APPOINTMENT,
        ScheduledNotification.Type.MASTER_CLIENT_REQUESTED_RESCHEDULE,
        ScheduledNotification.Type.MASTER_CLIENT_ACCEPTED_PROPOSAL,
        ScheduledNotification.Type.MASTER_CLIENT_DECLINED_PROPOSAL,
        ScheduledNotification.Type.MASTER_APPOINTMENT_PROPOSAL_EXPIRED,
        ScheduledNotification.Type.MASTER_MORNING_DIGEST,
        ScheduledNotification.Type.STALE_REQUESTED,
        ScheduledNotification.Type.FAILED_DELIVERY_TO_MASTER,
    }
