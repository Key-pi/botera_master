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
from django.utils import timezone

from apps.appointments.models import Appointment
from apps.bot.selectors import primary_master_telegram_contact
from apps.bot.services import (
    decrypt_bot_token,
    mark_master_contact_delivery_failure,
    mark_master_contact_reachable,
    render_content_template,
)
from apps.broadcasts.models import BroadcastRecipient
from apps.broadcasts.services import BroadcastDeliveryResult
from apps.messaging.models import ConversationMessage
from apps.notifications.models import ScheduledNotification
from apps.notifications.services import DeliveryResult
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

    if notification.type == ScheduledNotification.Type.STALE_REQUESTED:
        if notification.appointment is None:
            return None
        return _master_notification_payload(
            token=token,
            notification=notification,
            key="reminder.stale_requested",
            context=_appointment_template_context(notification.appointment)
            | {"request_age": _request_age_label(notification.appointment)},
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
    )


def _master_notification_payload(
    *,
    token: str,
    notification: ScheduledNotification,
    key: str,
    context: dict,
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
    }


def _appointment_services_label(appointment: Appointment) -> str:
    items = list(appointment.service_items.all())
    if not items:
        return ""
    return ", ".join(item.service_name_snapshot for item in items)


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
        ScheduledNotification.Type.MASTER_APPOINTMENT_REMINDER,
        ScheduledNotification.Type.MASTER_CLIENT_CANCELLED_APPOINTMENT,
        ScheduledNotification.Type.MASTER_MORNING_DIGEST,
        ScheduledNotification.Type.STALE_REQUESTED,
        ScheduledNotification.Type.FAILED_DELIVERY_TO_MASTER,
    }
