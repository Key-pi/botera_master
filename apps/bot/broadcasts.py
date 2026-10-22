from __future__ import annotations

from dataclasses import dataclass

from aiogram.types import InlineKeyboardMarkup
from django.core.exceptions import ValidationError

from apps.clients.models import ClientProfile
from apps.partners.models import Partner

from .booking import build_booking_categories_reply
from .profile import build_future_appointments_reply

BROADCAST_CALLBACK_PREFIX = "broadcast"


@dataclass(frozen=True, slots=True)
class BotBroadcastActionReply:
    """Response opened from a marketing broadcast call-to-action."""

    text: str
    reply_markup: InlineKeyboardMarkup | None = None
    starts_booking_flow: bool = False


def build_broadcast_action_reply(
    *,
    partner: Partner,
    client_profile: ClientProfile,
    callback_data: str,
) -> BotBroadcastActionReply:
    """Resolve a static broadcast action inside trusted partner/client scope."""

    if client_profile.partner_id != partner.id:
        raise ValidationError({"client_profile": "Client does not belong to this partner."})

    parts = callback_data.split(":")
    if len(parts) != 2 or parts[0] != BROADCAST_CALLBACK_PREFIX:
        raise ValidationError({"callback": "Unsupported broadcast callback."})

    action = parts[1]
    if action == "book":
        reply = build_booking_categories_reply(
            partner=partner,
            language=client_profile.language,
        )
        return BotBroadcastActionReply(
            text=reply.text,
            reply_markup=reply.reply_markup,
            starts_booking_flow=True,
        )
    if action == "my_appointments":
        reply = build_future_appointments_reply(
            partner=partner,
            client_profile=client_profile,
        )
        return BotBroadcastActionReply(
            text=reply.text,
            reply_markup=reply.reply_markup,
        )
    raise ValidationError({"callback": "Unsupported broadcast action."})
