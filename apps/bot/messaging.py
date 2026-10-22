from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from django.core.exceptions import ValidationError

from apps.accounts.models import User
from apps.accounts.services import ensure_partner_access
from apps.messaging.models import ConversationMessage
from apps.messaging.selectors import conversation_message_for_partner_by_id
from apps.messaging.services import create_master_reply_to_client
from apps.partners.models import Partner

from .services import render_content_template

MASTER_MESSAGE_CALLBACK_PREFIX = "mm"


@dataclass(frozen=True, slots=True)
class BotConversationReply:
    """Text and inline keyboard for one verified master messaging action."""

    text: str
    reply_markup: InlineKeyboardMarkup | None = None


def master_client_message_markup(
    *,
    message_id: UUID,
    language: str,
) -> InlineKeyboardMarkup:
    """Attach a compact reply action to a client-to-master delivery."""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_label("reply", language),
                    callback_data=_callback("reply", message_id),
                )
            ]
        ]
    )


def build_master_conversation_reply_prompt(
    *,
    partner: Partner,
    actor: User,
    source_message_id: UUID,
    invalid_message: bool = False,
) -> BotConversationReply:
    """Prompt a verified master to answer one partner-scoped client message."""

    source = _client_message_for_master(
        partner=partner,
        actor=actor,
        source_message_id=source_message_id,
    )
    prompt = _render(
        partner=partner,
        key="messaging.master_reply_prompt",
        context={"client_name": source.client_profile.display_name},
    )
    if invalid_message:
        invalid = _render(
            partner=partner,
            key="messaging.master_reply_invalid",
        )
        prompt = f"{invalid}\n\n{prompt}"
    return BotConversationReply(
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
    )


def queue_master_conversation_reply(
    *,
    partner: Partner,
    actor: User,
    source_message_id: UUID,
    text: str,
) -> BotConversationReply:
    """Queue a reply to the client from a freshly resolved source message."""

    source = _client_message_for_master(
        partner=partner,
        actor=actor,
        source_message_id=source_message_id,
    )
    create_master_reply_to_client(
        actor=actor,
        client_profile=source.client_profile,
        text=text,
    )
    return BotConversationReply(
        text=_render(
            partner=partner,
            key="messaging.master_reply_queued",
            context={"client_name": source.client_profile.display_name},
        )
    )


def cancel_master_conversation_reply(
    *,
    partner: Partner,
    actor: User,
    source_message_id: UUID,
) -> BotConversationReply:
    """Cancel a reply prompt only after rechecking its tenant-owned source."""

    _client_message_for_master(
        partner=partner,
        actor=actor,
        source_message_id=source_message_id,
    )
    return BotConversationReply(
        text=_render(
            partner=partner,
            key="messaging.master_reply_cancelled",
        )
    )


def parse_master_message_id(token: str) -> UUID:
    """Parse one compact UUID token without accepting arbitrary callback data."""

    try:
        return UUID(hex=token)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"callback": "Invalid conversation message id."}) from exc


def _client_message_for_master(
    *,
    partner: Partner,
    actor: User,
    source_message_id: UUID,
) -> ConversationMessage:
    ensure_partner_access(actor, partner)
    source = conversation_message_for_partner_by_id(
        partner=partner,
        message_id=source_message_id,
    )
    if source is None or source.direction != ConversationMessage.Direction.CLIENT_TO_MASTER:
        raise ValidationError({"message": "Client message is unavailable."})
    return source


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


def _callback(action: str, message_id: UUID) -> str:
    return f"{MASTER_MESSAGE_CALLBACK_PREFIX}:{action}:{message_id.hex}"


def _label(key: str, language: str) -> str:
    labels = {
        "reply": {"uk": "Відповісти", "ru": "Ответить", "en": "Reply"},
        "cancel": {"uk": "Скасувати", "ru": "Отменить", "en": "Cancel"},
    }
    return labels[key].get(language, labels[key]["uk"])
