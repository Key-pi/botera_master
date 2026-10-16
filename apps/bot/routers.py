from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message
from asgiref.sync import sync_to_async
from django.core.exceptions import ValidationError

from apps.bot.booking import (
    BOOKING_CALLBACK_PREFIX,
    BotBookingReply,
    build_booking_categories_reply,
    build_booking_dates_reply,
    build_booking_final_confirm_reply,
    build_booking_services_reply,
    build_booking_slots_reply,
    build_booking_summary_reply,
    confirm_booking_reply,
    parse_date_token,
    parse_page_token,
    parse_timestamp_token,
    parse_uuid_token,
)
from apps.bot.profile import (
    PROFILE_CALLBACK_PREFIX,
    BotProfileReply,
    build_appointment_details_reply,
    build_appointment_history_reply,
    build_cancel_appointment_reply,
    build_future_appointments_reply,
    build_profile_language_reply,
    build_profile_main_reply,
    confirm_cancel_appointment_reply,
    set_profile_language_reply,
)
from apps.bot.profile import (
    parse_page_token as parse_profile_page_token,
)
from apps.bot.profile import (
    parse_uuid_token as parse_profile_uuid_token,
)
from apps.bot.services import (
    handle_start_command,
    render_content_template,
    render_invalid_master_contact_link_message,
    resolve_client_profile_from_telegram_user,
)
from apps.partners.models import Partner


def build_partner_router() -> Router:
    """Build bot handlers that rely on trusted partner context middleware."""

    router = Router(name="partner")

    @router.message(CommandStart())
    async def start_handler(
        message: Message,
        command: CommandObject,
        partner: Partner,
    ) -> None:
        if message.from_user is None:
            return

        user = message.from_user
        payload = command.args or ""
        try:
            result = await sync_to_async(handle_start_command)(
                partner=partner,
                telegram_user_id=user.id,
                username=user.username or "",
                first_name=user.first_name or "",
                last_name=user.last_name or "",
                is_premium=bool(getattr(user, "is_premium", False)),
                language_code=user.language_code or "",
                start_payload=payload,
            )
            await message.answer(result.text)
        except ValidationError:
            text = await sync_to_async(render_invalid_master_contact_link_message)(partner=partner)
            await message.answer(text)

    @router.message(Command("book"))
    async def book_handler(message: Message, partner: Partner) -> None:
        profile = await _client_profile_for_message(message=message, partner=partner)
        if profile is None:
            return
        reply = await sync_to_async(build_booking_categories_reply)(
            partner=partner,
            language=profile.language,
        )
        await message.answer(reply.text, reply_markup=reply.reply_markup)

    @router.message(Command("profile"))
    async def profile_handler(message: Message, partner: Partner) -> None:
        profile = await _client_profile_for_message(message=message, partner=partner)
        if profile is None:
            return
        reply = await sync_to_async(build_profile_main_reply)(
            partner=partner,
            client_profile=profile,
        )
        await message.answer(reply.text, reply_markup=reply.reply_markup)

    @router.message(Command("history"))
    async def history_handler(message: Message, partner: Partner) -> None:
        profile = await _client_profile_for_message(message=message, partner=partner)
        if profile is None:
            return
        reply = await sync_to_async(build_appointment_history_reply)(
            partner=partner,
            client_profile=profile,
        )
        await message.answer(reply.text, reply_markup=reply.reply_markup)

    @router.message(Command("language"))
    async def language_handler(message: Message, partner: Partner) -> None:
        profile = await _client_profile_for_message(message=message, partner=partner)
        if profile is None:
            return
        reply = await sync_to_async(build_profile_language_reply)(
            partner=partner,
            client_profile=profile,
        )
        await message.answer(reply.text, reply_markup=reply.reply_markup)

    @router.callback_query(F.data.startswith(f"{BOOKING_CALLBACK_PREFIX}:"))
    async def booking_callback_handler(callback: CallbackQuery, partner: Partner) -> None:
        if callback.data == f"{BOOKING_CALLBACK_PREFIX}:noop":
            await callback.answer()
            return

        profile = await _client_profile_for_callback(callback=callback, partner=partner)
        if profile is None or callback.message is None or callback.data is None:
            return

        try:
            reply = await sync_to_async(_booking_reply_from_callback_data)(
                partner=partner,
                client_profile=profile,
                data=callback.data,
            )
        except ValidationError:
            reply = await sync_to_async(_invalid_booking_action_reply)(
                partner=partner,
                language=profile.language,
            )

        await callback.message.edit_text(reply.text, reply_markup=reply.reply_markup)
        await callback.answer()

    @router.callback_query(F.data.startswith(f"{PROFILE_CALLBACK_PREFIX}:"))
    async def profile_callback_handler(callback: CallbackQuery, partner: Partner) -> None:
        if callback.data == f"{PROFILE_CALLBACK_PREFIX}:noop":
            await callback.answer()
            return

        profile = await _client_profile_for_callback(callback=callback, partner=partner)
        if profile is None or callback.message is None or callback.data is None:
            return

        try:
            reply = await sync_to_async(_profile_reply_from_callback_data)(
                partner=partner,
                client_profile=profile,
                data=callback.data,
            )
        except ValidationError:
            reply = await sync_to_async(_invalid_profile_action_reply)(
                partner=partner,
                language=profile.language,
            )

        await callback.message.edit_text(reply.text, reply_markup=reply.reply_markup)
        await callback.answer()

    return router


def _booking_reply_from_callback_data(*, partner: Partner, client_profile, data: str):
    parts = data.split(":")
    if len(parts) < 2 or parts[0] != BOOKING_CALLBACK_PREFIX:
        raise ValidationError({"callback": "Unsupported callback."})

    action = parts[1]
    language = client_profile.language
    if action == "cat" and len(parts) == 3:
        return build_booking_categories_reply(
            partner=partner,
            language=language,
            page=parse_page_token(parts[2]),
        )
    if action == "svc" and len(parts) == 4:
        return build_booking_services_reply(
            partner=partner,
            category_id=parse_uuid_token(parts[2]),
            language=language,
            page=parse_page_token(parts[3]),
        )
    if action == "sum" and len(parts) == 3:
        return build_booking_summary_reply(
            partner=partner,
            service_id=parse_uuid_token(parts[2]),
            language=language,
        )
    if action == "dates" and len(parts) == 4:
        return build_booking_dates_reply(
            partner=partner,
            service_id=parse_uuid_token(parts[2]),
            language=language,
            page=parse_page_token(parts[3]),
        )
    if action == "slots" and len(parts) == 5:
        return build_booking_slots_reply(
            partner=partner,
            service_id=parse_uuid_token(parts[2]),
            local_date=parse_date_token(parts[3]),
            language=language,
            page=parse_page_token(parts[4]),
        )
    if action == "final" and len(parts) == 4:
        return build_booking_final_confirm_reply(
            partner=partner,
            service_id=parse_uuid_token(parts[2]),
            start_at=parse_timestamp_token(parts[3]),
            language=language,
        )
    if action == "create" and len(parts) == 4:
        return confirm_booking_reply(
            partner=partner,
            client_profile=client_profile,
            service_id=parse_uuid_token(parts[2]),
            start_at=parse_timestamp_token(parts[3]),
            language=language,
        )

    raise ValidationError({"callback": "Unsupported callback."})


def _invalid_booking_action_reply(*, partner: Partner, language: str):
    rendered = render_content_template(
        partner=partner,
        key="booking.slot_taken",
        language=language,
    )
    return BotBookingReply(text=rendered.body)


def _profile_reply_from_callback_data(*, partner: Partner, client_profile, data: str):
    parts = data.split(":")
    if len(parts) < 2 or parts[0] != PROFILE_CALLBACK_PREFIX:
        raise ValidationError({"callback": "Unsupported callback."})

    action = parts[1]
    if action == "main" and len(parts) == 2:
        return build_profile_main_reply(partner=partner, client_profile=client_profile)
    if action == "future" and len(parts) == 3:
        return build_future_appointments_reply(
            partner=partner,
            client_profile=client_profile,
            page=parse_profile_page_token(parts[2]),
        )
    if action == "history" and len(parts) == 3:
        return build_appointment_history_reply(
            partner=partner,
            client_profile=client_profile,
            page=parse_profile_page_token(parts[2]),
        )
    if action == "details" and len(parts) == 5:
        return_action = parts[3]
        _validate_profile_return_action(return_action)
        return build_appointment_details_reply(
            partner=partner,
            client_profile=client_profile,
            appointment_id=parse_profile_uuid_token(parts[2]),
            return_action=return_action,
            return_page=parse_profile_page_token(parts[4]),
        )
    if action == "cancel" and len(parts) == 5:
        return_action = parts[3]
        _validate_profile_return_action(return_action)
        return build_cancel_appointment_reply(
            partner=partner,
            client_profile=client_profile,
            appointment_id=parse_profile_uuid_token(parts[2]),
            return_action=return_action,
            return_page=parse_profile_page_token(parts[4]),
        )
    if action == "cancelgo" and len(parts) == 6:
        return_action = parts[3]
        _validate_profile_return_action(return_action)
        if parts[5] not in {"0", "1"}:
            raise ValidationError({"callback": "Invalid cancellation confirmation."})
        return confirm_cancel_appointment_reply(
            partner=partner,
            client_profile=client_profile,
            appointment_id=parse_profile_uuid_token(parts[2]),
            return_action=return_action,
            return_page=parse_profile_page_token(parts[4]),
            accept_late_penalty=parts[5] == "1",
        )
    if action == "lang" and len(parts) == 2:
        return build_profile_language_reply(partner=partner, client_profile=client_profile)
    if action == "langset" and len(parts) == 3:
        return set_profile_language_reply(
            partner=partner,
            client_profile=client_profile,
            language=parts[2],
        )

    raise ValidationError({"callback": "Unsupported callback."})


def _invalid_profile_action_reply(*, partner: Partner, language: str):
    rendered = render_content_template(
        partner=partner,
        key="error.invalid_action",
        language=language,
    )
    return BotProfileReply(text=rendered.body)


def _validate_profile_return_action(return_action: str) -> None:
    if return_action not in {"future", "history"}:
        raise ValidationError({"callback": "Unsupported return target."})


async def _client_profile_for_message(*, message: Message, partner: Partner):
    if message.from_user is None:
        return None
    _identity, profile = await sync_to_async(resolve_client_profile_from_telegram_user)(
        partner=partner,
        telegram_user_id=message.from_user.id,
        username=message.from_user.username or "",
        first_name=message.from_user.first_name or "",
        last_name=message.from_user.last_name or "",
        is_premium=bool(getattr(message.from_user, "is_premium", False)),
        language_code=message.from_user.language_code or "",
    )
    return profile


async def _client_profile_for_callback(*, callback: CallbackQuery, partner: Partner):
    _identity, profile = await sync_to_async(resolve_client_profile_from_telegram_user)(
        partner=partner,
        telegram_user_id=callback.from_user.id,
        username=callback.from_user.username or "",
        first_name=callback.from_user.first_name or "",
        last_name=callback.from_user.last_name or "",
        is_premium=bool(getattr(callback.from_user, "is_premium", False)),
        language_code=callback.from_user.language_code or "",
    )
    return profile
