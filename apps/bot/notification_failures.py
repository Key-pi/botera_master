from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from django.core.exceptions import ValidationError

from apps.accounts.models import User
from apps.accounts.services import ensure_partner_access
from apps.clients.selectors import client_operational_summary
from apps.messaging.services import create_master_reply_to_client
from apps.notifications.models import ScheduledNotification
from apps.notifications.selectors import notification_for_partner_by_id
from apps.notifications.services import DeliveryResult, retry_failed_notification
from apps.partners.models import Partner

from .services import render_content_template

FAILED_NOTIFICATION_CALLBACK_PREFIX = "nf"

DeliverNotification = Callable[[ScheduledNotification], DeliveryResult]


@dataclass(frozen=True, slots=True)
class BotFailedDeliveryReply:
    """Text and controls for one verified failed-reminder action."""

    text: str
    reply_markup: InlineKeyboardMarkup | None = None
    wait_for_text: bool = False


def failed_delivery_to_master_markup(
    *,
    source_notification_id: UUID,
    language: str,
) -> InlineKeyboardMarkup:
    """Build compact actions for a failed client reminder."""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_label("retry", language),
                    callback_data=_callback("retry", source_notification_id),
                ),
                InlineKeyboardButton(
                    text=_label("message", language),
                    callback_data=_callback("message", source_notification_id),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_label("client", language),
                    callback_data=_callback("client", source_notification_id),
                )
            ],
        ]
    )


def retry_failed_client_reminder_reply(
    *,
    partner: Partner,
    actor: User,
    source_notification_id: UUID,
    deliver: DeliverNotification,
) -> BotFailedDeliveryReply:
    """Retry one partner-scoped failed reminder without hiding a new failure."""

    source = _failed_client_reminder_for_master(
        partner=partner,
        actor=actor,
        source_notification_id=source_notification_id,
    )
    retried = retry_failed_notification(notification=source, deliver=deliver)
    if retried.status == ScheduledNotification.Status.SENT:
        return BotFailedDeliveryReply(
            text=_render(partner=partner, key="reminder.manual_retry_sent")
        )
    if retried.status in {
        ScheduledNotification.Status.SKIPPED,
        ScheduledNotification.Status.STALE,
        ScheduledNotification.Status.CANCELLED,
    }:
        return BotFailedDeliveryReply(
            text=_render(partner=partner, key="reminder.manual_retry_skipped")
        )

    reason = business_delivery_failure_reason(
        failure_code=retried.last_failure_code,
        language=partner.default_language,
    )
    return BotFailedDeliveryReply(
        text=_render(
            partner=partner,
            key="reminder.manual_retry_failed",
            context={"failure_reason": reason},
        ),
        reply_markup=failed_delivery_to_master_markup(
            source_notification_id=retried.id,
            language=partner.default_language,
        ),
    )


def build_failed_client_summary_reply(
    *,
    partner: Partner,
    actor: User,
    source_notification_id: UUID,
) -> BotFailedDeliveryReply:
    """Render a compact client card from the failed reminder's trusted source."""

    source = _failed_client_reminder_for_master(
        partner=partner,
        actor=actor,
        source_notification_id=source_notification_id,
    )
    client = source.client_profile
    summary = client_operational_summary(client_profile=client)
    return BotFailedDeliveryReply(
        text=_render(
            partner=partner,
            key="reminder.failed_client_summary",
            context={
                "client_name": client.display_name,
                "client_phone": client.phone or "-",
                "visit_count": summary.completed_visit_count,
                "future_count": summary.future_appointment_count,
                "penalty_count": summary.outstanding_penalty_count,
                "telegram_status": _telegram_status(
                    client=client,
                    language=partner.default_language,
                ),
            },
        )
    )


def build_failed_client_message_prompt_reply(
    *,
    partner: Partner,
    actor: User,
    source_notification_id: UUID,
    invalid_message: bool = False,
) -> BotFailedDeliveryReply:
    """Prompt the master for a manual message when the client channel is usable."""

    source = _failed_client_reminder_for_master(
        partner=partner,
        actor=actor,
        source_notification_id=source_notification_id,
    )
    client = source.client_profile
    if not client.telegram_identity_id or client.bot_unreachable:
        return _unavailable_client_reply(partner=partner, client_name=client.display_name)

    prompt = _render(
        partner=partner,
        key="messaging.master_reply_prompt",
        context={"client_name": client.display_name},
    )
    if invalid_message:
        invalid = _render(partner=partner, key="messaging.master_reply_invalid")
        prompt = f"{invalid}\n\n{prompt}"
    return BotFailedDeliveryReply(
        text=prompt,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=_label("cancel", partner.default_language),
                        callback_data=_callback("cancel", source.id),
                    )
                ]
            ]
        ),
        wait_for_text=True,
    )


def queue_failed_client_message_reply(
    *,
    partner: Partner,
    actor: User,
    source_notification_id: UUID,
    text: str,
) -> BotFailedDeliveryReply:
    """Queue a manual master message after rechecking source and reachability."""

    source = _failed_client_reminder_for_master(
        partner=partner,
        actor=actor,
        source_notification_id=source_notification_id,
    )
    client = source.client_profile
    if not client.telegram_identity_id or client.bot_unreachable:
        return _unavailable_client_reply(partner=partner, client_name=client.display_name)

    create_master_reply_to_client(
        actor=actor,
        client_profile=client,
        text=text,
    )
    return BotFailedDeliveryReply(
        text=_render(
            partner=partner,
            key="messaging.master_reply_queued",
            context={"client_name": client.display_name},
        )
    )


def cancel_failed_client_message_reply(
    *,
    partner: Partner,
    actor: User,
    source_notification_id: UUID,
) -> BotFailedDeliveryReply:
    """Cancel the prompt after verifying that its source is still actionable."""

    _failed_client_reminder_for_master(
        partner=partner,
        actor=actor,
        source_notification_id=source_notification_id,
    )
    return BotFailedDeliveryReply(
        text=_render(partner=partner, key="messaging.master_reply_cancelled")
    )


def parse_failed_notification_id(token: str) -> UUID:
    """Parse a compact UUID token without accepting arbitrary callback data."""

    try:
        return UUID(hex=token)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"callback": "Invalid notification id."}) from exc


def business_delivery_failure_reason(*, failure_code: str, language: str) -> str:
    """Translate adapter failure codes without exposing Telegram error payloads."""

    reason_key_by_code = {
        "bot_blocked": "blocked",
        "user_deactivated": "deactivated",
        "chat_not_found": "chat_not_found",
        "telegram_429": "rate_limited",
        "telegram_network_error": "temporary",
        "telegram_timeout": "temporary",
        "bot_unauthorized": "bot_error",
        "telegram_bad_request": "telegram_error",
        "telegram_api_error": "telegram_error",
    }
    reason_key = reason_key_by_code.get(failure_code, "temporary")
    labels = {
        "blocked": {
            "uk": "клієнт заблокував бота",
            "ru": "клиент заблокировал бота",
            "en": "the client blocked the bot",
        },
        "deactivated": {
            "uk": "Telegram-акаунт клієнта деактивовано",
            "ru": "Telegram-аккаунт клиента деактивирован",
            "en": "the client's Telegram account is deactivated",
        },
        "chat_not_found": {
            "uk": "Telegram-чат клієнта недоступний",
            "ru": "Telegram-чат клиента недоступен",
            "en": "the client's Telegram chat is unavailable",
        },
        "rate_limited": {
            "uk": "Telegram тимчасово обмежив частоту надсилання",
            "ru": "Telegram временно ограничил частоту отправки",
            "en": "Telegram temporarily rate-limited delivery",
        },
        "bot_error": {
            "uk": "потрібно перевірити підключення бота",
            "ru": "нужно проверить подключение бота",
            "en": "the bot connection needs attention",
        },
        "telegram_error": {
            "uk": "Telegram відхилив повідомлення",
            "ru": "Telegram отклонил сообщение",
            "en": "Telegram rejected the message",
        },
        "temporary": {
            "uk": "тимчасова помилка доставки Telegram",
            "ru": "временная ошибка доставки Telegram",
            "en": "a temporary Telegram delivery error",
        },
    }
    return labels[reason_key].get(language, labels[reason_key]["uk"])


def _failed_client_reminder_for_master(
    *,
    partner: Partner,
    actor: User,
    source_notification_id: UUID,
) -> ScheduledNotification:
    ensure_partner_access(actor, partner)
    source = notification_for_partner_by_id(
        partner=partner,
        notification_id=source_notification_id,
    )
    if (
        source is None
        or source.type != ScheduledNotification.Type.CLIENT_APPOINTMENT_REMINDER
        or source.status != ScheduledNotification.Status.FAILED
        or not source.failed_visible_to_partner
        or source.appointment_id is None
        or source.client_profile_id is None
    ):
        raise ValidationError({"notification": "Failed reminder is unavailable."})
    return source


def _unavailable_client_reply(*, partner: Partner, client_name: str) -> BotFailedDeliveryReply:
    return BotFailedDeliveryReply(
        text=_render(
            partner=partner,
            key="messaging.master_reply_unavailable",
            context={"client_name": client_name},
        )
    )


def _telegram_status(*, client, language: str) -> str:
    status = "unavailable" if client.bot_unreachable else "connected"
    if not client.telegram_identity_id:
        status = "not_linked"
    labels = {
        "connected": {"uk": "підключено", "ru": "подключён", "en": "connected"},
        "unavailable": {"uk": "недоступний", "ru": "недоступен", "en": "unavailable"},
        "not_linked": {"uk": "не підключено", "ru": "не подключён", "en": "not linked"},
    }
    return labels[status].get(language, labels[status]["uk"])


def _render(
    *,
    partner: Partner,
    key: str,
    context: dict | None = None,
) -> str:
    return render_content_template(
        partner=partner,
        key=key,
        language=partner.default_language,
        context=context,
    ).body


def _callback(action: str, notification_id: UUID) -> str:
    return f"{FAILED_NOTIFICATION_CALLBACK_PREFIX}:{action}:{notification_id.hex}"


def _label(key: str, language: str) -> str:
    labels = {
        "retry": {"uk": "Надіслати ще раз", "ru": "Отправить ещё раз", "en": "Retry"},
        "message": {"uk": "Написати клієнту", "ru": "Написать клиенту", "en": "Message client"},
        "client": {"uk": "Переглянути клієнта", "ru": "Посмотреть клиента", "en": "View client"},
        "cancel": {"uk": "Скасувати", "ru": "Отменить", "en": "Cancel"},
    }
    return labels[key].get(language, labels[key]["uk"])
