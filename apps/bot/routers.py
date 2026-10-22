from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from asgiref.sync import sync_to_async
from django.core.exceptions import ValidationError

from apps.bot.booking import (
    BOOKING_CALLBACK_PREFIX,
    BotBookingReply,
    booking_requires_phone,
    booking_selection_signature,
    build_booking_categories_reply,
    build_booking_comment_prompt_reply,
    build_booking_dates_reply,
    build_booking_final_confirm_reply,
    build_booking_month_dates_reply,
    build_booking_months_reply,
    build_booking_required_phone_reply,
    build_booking_services_reply,
    build_booking_slot_taken_reply,
    build_booking_slots_reply,
    build_booking_summary_reply,
    confirm_booking_reply,
    normalize_booking_comment,
    parse_date_token,
    parse_month_token,
    parse_page_token,
    parse_timestamp_token,
    parse_uuid_token,
    remove_booking_service_selection,
    toggle_booking_service_selection,
    validate_date_source_token,
)
from apps.bot.broadcasts import BROADCAST_CALLBACK_PREFIX, build_broadcast_action_reply
from apps.bot.delivery import deliver_scheduled_notification
from apps.bot.master import (
    MASTER_REQUEST_CALLBACK_PREFIX,
    BotMasterReply,
    build_master_booking_request_action_failed_reply,
    build_master_booking_request_decline_reply,
    build_master_booking_request_reply,
    build_master_requested_alternative_dates_reply,
    build_master_requested_alternative_review_reply,
    build_master_requested_alternative_slots_reply,
    confirm_master_booking_request_reply,
    decline_master_booking_request_reply,
    parse_master_proposal_date_token,
    parse_master_proposal_page_token,
    parse_master_proposal_timestamp_token,
    parse_master_request_appointment_id,
    send_master_requested_alternative_proposal_reply,
    toggle_master_requested_alternative_slot_reply,
)
from apps.bot.messaging import (
    MASTER_MESSAGE_CALLBACK_PREFIX,
    BotConversationReply,
    build_master_conversation_reply_prompt,
    cancel_master_conversation_reply,
    parse_master_message_id,
    queue_master_conversation_reply,
)
from apps.bot.notification_failures import (
    FAILED_NOTIFICATION_CALLBACK_PREFIX,
    BotFailedDeliveryReply,
    build_failed_client_message_prompt_reply,
    build_failed_client_summary_reply,
    cancel_failed_client_message_reply,
    parse_failed_notification_id,
    queue_failed_client_message_reply,
    retry_failed_client_reminder_reply,
)
from apps.bot.profile import (
    PROFILE_CALLBACK_PREFIX,
    BotProfileReply,
    accept_appointment_proposal_reply,
    build_appointment_details_reply,
    build_appointment_history_reply,
    build_appointment_proposal_reply,
    build_cancel_appointment_reply,
    build_contact_master_prompt_reply,
    build_future_appointments_reply,
    build_profile_language_reply,
    build_profile_main_reply,
    build_requested_alternative_dates_reply,
    build_requested_alternative_final_confirm_reply,
    build_requested_alternative_slots_reply,
    build_reschedule_dates_reply,
    build_reschedule_final_confirm_reply,
    build_reschedule_slots_reply,
    confirm_cancel_appointment_reply,
    confirm_requested_alternative_reply,
    confirm_reschedule_reply,
    decline_appointment_proposal_reply,
    parse_return_action_token,
    send_client_message_to_master_reply,
    set_profile_language_reply,
)
from apps.bot.profile import (
    parse_integer_token as parse_profile_integer_token,
)
from apps.bot.profile import (
    parse_page_token as parse_profile_page_token,
)
from apps.bot.profile import (
    parse_uuid_token as parse_profile_uuid_token,
)
from apps.bot.selectors import master_telegram_contact_for_user
from apps.bot.services import (
    handle_start_command,
    render_content_template,
    render_invalid_master_contact_link_message,
    resolve_client_profile_from_telegram_user,
)
from apps.clients.services import update_client_profile_phone_from_telegram
from apps.partners.models import Partner
from core.timezones import to_partner_time


class ClientMessageState(StatesGroup):
    """Ephemeral input state before a durable conversation message exists."""

    waiting_for_text = State()


class BookingFlowState(StatesGroup):
    """Ephemeral multi-service selection for one active booking conversation."""

    active = State()
    waiting_for_comment = State()
    waiting_for_phone = State()


class MasterProposalFlowState(StatesGroup):
    """Ephemeral alternative-slot selection for one master booking request."""

    active = State()


class MasterMessageFlowState(StatesGroup):
    """Ephemeral master input before a durable reply is queued."""

    waiting_for_text = State()


class FailedNotificationMessageFlowState(StatesGroup):
    """Ephemeral master input for a client whose reminder failed."""

    waiting_for_text = State()


@dataclass(frozen=True, slots=True)
class BookingCallbackResult:
    """Booking adapter response plus the next ephemeral FSM state."""

    reply: BotBookingReply
    selected_service_ids: tuple[UUID, ...]
    summary_signature: str = ""
    client_comment: str | None = None
    input_mode: str = ""
    pending_start_at: datetime | None = None
    date_source: str = ""
    complete: bool = False


@dataclass(frozen=True, slots=True)
class BookingStateValues:
    """Validated values restored from one Telegram FSM booking conversation."""

    selected_service_ids: tuple[UUID, ...] = ()
    summary_signature: str = ""
    client_comment: str = ""
    pending_start_at: datetime | None = None
    date_source: str = ""


@dataclass(frozen=True, slots=True)
class MasterCallbackResult:
    """Master adapter response plus its next ephemeral proposal state."""

    reply: BotMasterReply
    appointment_id: UUID | None = None
    selected_start_ats: tuple[datetime, ...] = ()
    keep_proposal_state: bool = False


@dataclass(frozen=True, slots=True)
class MasterProposalStateValues:
    """Validated values restored from one master proposal conversation."""

    appointment_id: UUID | None = None
    selected_start_ats: tuple[datetime, ...] = ()


@dataclass(frozen=True, slots=True)
class MasterMessageCallbackResult:
    """Master messaging response plus the next ephemeral input state."""

    reply: BotConversationReply
    source_message_id: UUID | None = None
    wait_for_text: bool = False


@dataclass(frozen=True, slots=True)
class FailedNotificationCallbackResult:
    """Failed-reminder response plus its next Telegram presentation mode."""

    reply: BotFailedDeliveryReply
    source_notification_id: UUID | None = None
    wait_for_text: bool = False
    send_new_message: bool = False


BOOKING_SERVICE_IDS_STATE_KEY = "booking_service_ids"
BOOKING_SUMMARY_SIGNATURE_STATE_KEY = "booking_summary_signature"
BOOKING_COMMENT_STATE_KEY = "booking_comment"
BOOKING_PENDING_START_STATE_KEY = "booking_pending_start"
BOOKING_DATE_SOURCE_STATE_KEY = "booking_date_source"
BOOKING_INPUT_COMMENT = "comment"
BOOKING_INPUT_PHONE = "phone"
MASTER_PROPOSAL_APPOINTMENT_STATE_KEY = "master_proposal_appointment_id"
MASTER_PROPOSAL_STARTS_STATE_KEY = "master_proposal_start_ats"
MASTER_MESSAGE_SOURCE_STATE_KEY = "master_message_source_id"
FAILED_NOTIFICATION_SOURCE_STATE_KEY = "failed_notification_source_id"


def build_partner_router() -> Router:
    """Build bot handlers that rely on trusted partner context middleware."""

    router = Router(name="partner")

    @router.message(CommandStart())
    async def start_handler(
        message: Message,
        command: CommandObject,
        partner: Partner,
        state: FSMContext,
    ) -> None:
        await state.clear()
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
    async def book_handler(message: Message, partner: Partner, state: FSMContext) -> None:
        await state.clear()
        profile = await _client_profile_for_message(message=message, partner=partner)
        if profile is None:
            return
        reply = await sync_to_async(build_booking_categories_reply)(
            partner=partner,
            language=profile.language,
        )
        await _store_booking_state(
            state=state,
            selected_service_ids=(),
            summary_signature="",
        )
        await message.answer(reply.text, reply_markup=reply.reply_markup)

    @router.message(Command("profile"))
    async def profile_handler(message: Message, partner: Partner, state: FSMContext) -> None:
        await state.clear()
        profile = await _client_profile_for_message(message=message, partner=partner)
        if profile is None:
            return
        reply = await sync_to_async(build_profile_main_reply)(
            partner=partner,
            client_profile=profile,
        )
        await message.answer(reply.text, reply_markup=reply.reply_markup)

    @router.message(Command("history"))
    async def history_handler(message: Message, partner: Partner, state: FSMContext) -> None:
        await state.clear()
        profile = await _client_profile_for_message(message=message, partner=partner)
        if profile is None:
            return
        reply = await sync_to_async(build_appointment_history_reply)(
            partner=partner,
            client_profile=profile,
        )
        await message.answer(reply.text, reply_markup=reply.reply_markup)

    @router.message(Command("language"))
    async def language_handler(message: Message, partner: Partner, state: FSMContext) -> None:
        await state.clear()
        profile = await _client_profile_for_message(message=message, partner=partner)
        if profile is None:
            return
        reply = await sync_to_async(build_profile_language_reply)(
            partner=partner,
            client_profile=profile,
        )
        await message.answer(reply.text, reply_markup=reply.reply_markup)

    @router.message(BookingFlowState.waiting_for_comment)
    async def booking_comment_handler(
        message: Message,
        partner: Partner,
        state: FSMContext,
    ) -> None:
        profile = await _client_profile_for_message(message=message, partner=partner)
        booking_state = _booking_state_values(await state.get_data())
        if profile is None:
            await state.clear()
            return

        try:
            if message.text is None:
                raise ValidationError({"client_comment": "Booking comment must be text."})
            result = await sync_to_async(_booking_reply_from_comment_input)(
                partner=partner,
                client_profile=profile,
                booking_state=booking_state,
                text=message.text,
            )
        except ValidationError:
            if booking_state.pending_start_at is None or not booking_state.date_source:
                await state.clear()
                reply = await sync_to_async(_invalid_booking_action_reply)(
                    partner=partner,
                    language=profile.language,
                )
            else:
                reply = await sync_to_async(build_booking_comment_prompt_reply)(
                    partner=partner,
                    start_at=booking_state.pending_start_at,
                    language=profile.language,
                    date_source=booking_state.date_source,
                    client_comment=booking_state.client_comment,
                )
        else:
            reply = result.reply
            await _apply_booking_result_to_state(
                state=state,
                previous=booking_state,
                result=result,
            )
        await message.answer(reply.text, reply_markup=reply.reply_markup)

    @router.message(BookingFlowState.waiting_for_phone)
    async def booking_phone_handler(
        message: Message,
        partner: Partner,
        state: FSMContext,
    ) -> None:
        profile = await _client_profile_for_message(message=message, partner=partner)
        booking_state = _booking_state_values(await state.get_data())
        if profile is None:
            await state.clear()
            return

        phone = _phone_from_message(message)
        try:
            if phone is None:
                raise ValidationError({"phone": "Phone must be sent as text or own contact."})
            result = await sync_to_async(_booking_reply_from_phone_input)(
                partner=partner,
                client_profile=profile,
                booking_state=booking_state,
                phone=phone,
            )
        except ValidationError as exc:
            if (
                "phone" not in getattr(exc, "message_dict", {})
                or booking_state.pending_start_at is None
                or not booking_state.date_source
            ):
                await state.clear()
                reply = await sync_to_async(_invalid_booking_action_reply)(
                    partner=partner,
                    language=profile.language,
                )
            else:
                reply = await sync_to_async(build_booking_required_phone_reply)(
                    partner=partner,
                    start_at=booking_state.pending_start_at,
                    language=profile.language,
                    date_source=booking_state.date_source,
                )
        else:
            reply = result.reply
            await _apply_booking_result_to_state(
                state=state,
                previous=booking_state,
                result=result,
            )
        await message.answer(reply.text, reply_markup=reply.reply_markup)

    @router.message(ClientMessageState.waiting_for_text)
    async def contact_master_message_handler(
        message: Message,
        partner: Partner,
        state: FSMContext,
    ) -> None:
        profile = await _client_profile_for_message(message=message, partner=partner)
        if profile is None:
            await state.clear()
            return

        if message.text is None:
            reply = await sync_to_async(build_contact_master_prompt_reply)(
                partner=partner,
                client_profile=profile,
                invalid_message=True,
            )
        else:
            try:
                reply = await sync_to_async(send_client_message_to_master_reply)(
                    partner=partner,
                    client_profile=profile,
                    text=message.text,
                )
            except ValidationError:
                reply = await sync_to_async(build_contact_master_prompt_reply)(
                    partner=partner,
                    client_profile=profile,
                    invalid_message=True,
                )
            else:
                await state.clear()
        await message.answer(reply.text, reply_markup=reply.reply_markup)

    @router.message(MasterMessageFlowState.waiting_for_text)
    async def master_conversation_reply_handler(
        message: Message,
        partner: Partner,
        state: FSMContext,
    ) -> None:
        if message.from_user is None:
            await state.clear()
            return
        contact = await sync_to_async(master_telegram_contact_for_user)(
            partner=partner,
            telegram_user_id=message.from_user.id,
        )
        source_message_id = _master_message_state_id(await state.get_data())
        if contact is None or contact.user is None or source_message_id is None:
            await state.clear()
            reply = await sync_to_async(_invalid_master_message_action_reply)(partner=partner)
            await message.answer(reply.text)
            return

        try:
            if message.text is None:
                reply = await sync_to_async(build_master_conversation_reply_prompt)(
                    partner=partner,
                    actor=contact.user,
                    source_message_id=source_message_id,
                    invalid_message=True,
                )
            else:
                reply = await sync_to_async(queue_master_conversation_reply)(
                    partner=partner,
                    actor=contact.user,
                    source_message_id=source_message_id,
                    text=message.text,
                )
        except ValidationError:
            await state.clear()
            reply = await sync_to_async(_invalid_master_message_action_reply)(partner=partner)
        else:
            if message.text is not None:
                await state.clear()
        await message.answer(reply.text, reply_markup=reply.reply_markup)

    @router.message(FailedNotificationMessageFlowState.waiting_for_text)
    async def failed_notification_message_handler(
        message: Message,
        partner: Partner,
        state: FSMContext,
    ) -> None:
        if message.from_user is None:
            await state.clear()
            return
        contact = await sync_to_async(master_telegram_contact_for_user)(
            partner=partner,
            telegram_user_id=message.from_user.id,
        )
        source_notification_id = _failed_notification_state_id(await state.get_data())
        if contact is None or contact.user is None or source_notification_id is None:
            await state.clear()
            reply = await sync_to_async(_invalid_failed_notification_action_reply)(partner=partner)
            await message.answer(reply.text)
            return

        try:
            if message.text is None:
                reply = await sync_to_async(build_failed_client_message_prompt_reply)(
                    partner=partner,
                    actor=contact.user,
                    source_notification_id=source_notification_id,
                    invalid_message=True,
                )
            else:
                reply = await sync_to_async(queue_failed_client_message_reply)(
                    partner=partner,
                    actor=contact.user,
                    source_notification_id=source_notification_id,
                    text=message.text,
                )
        except ValidationError:
            await state.clear()
            reply = await sync_to_async(_invalid_failed_notification_action_reply)(partner=partner)
        else:
            if message.text is not None or not reply.wait_for_text:
                await state.clear()
        await message.answer(reply.text, reply_markup=reply.reply_markup)

    @router.callback_query(F.data.startswith(f"{BROADCAST_CALLBACK_PREFIX}:"))
    async def broadcast_callback_handler(
        callback: CallbackQuery,
        partner: Partner,
        state: FSMContext,
    ) -> None:
        profile = await _client_profile_for_callback(callback=callback, partner=partner)
        if profile is None or callback.message is None or callback.data is None:
            return

        try:
            reply = await sync_to_async(build_broadcast_action_reply)(
                partner=partner,
                client_profile=profile,
                callback_data=callback.data,
            )
        except ValidationError:
            await state.clear()
            rendered = await sync_to_async(render_content_template)(
                partner=partner,
                key="error.invalid_action",
                language=profile.language,
            )
            await callback.answer(rendered.body[:200], show_alert=True)
            return

        if reply.starts_booking_flow:
            await _store_booking_state(
                state=state,
                selected_service_ids=(),
                summary_signature="",
            )
        else:
            await state.clear()
        # Broadcasts may contain a photo, so their original message is not a
        # reliable edit target. Open the selected flow in a fresh bot message.
        await callback.message.answer(reply.text, reply_markup=reply.reply_markup)
        await callback.answer()

    @router.callback_query(F.data.startswith(f"{BOOKING_CALLBACK_PREFIX}:"))
    async def booking_callback_handler(
        callback: CallbackQuery,
        partner: Partner,
        state: FSMContext,
    ) -> None:
        if callback.data == f"{BOOKING_CALLBACK_PREFIX}:noop":
            await callback.answer()
            return

        profile = await _client_profile_for_callback(callback=callback, partner=partner)
        if profile is None or callback.message is None or callback.data is None:
            return

        booking_state = _booking_state_values(await state.get_data())
        try:
            result = await sync_to_async(_booking_reply_from_callback_data)(
                partner=partner,
                client_profile=profile,
                data=callback.data,
                selected_service_ids=booking_state.selected_service_ids,
                summary_signature=booking_state.summary_signature,
                client_comment=booking_state.client_comment,
            )
        except ValidationError:
            await state.clear()
            reply = await sync_to_async(_invalid_booking_action_reply)(
                partner=partner,
                language=profile.language,
            )
        else:
            reply = result.reply
            await _apply_booking_result_to_state(
                state=state,
                previous=booking_state,
                result=result,
            )

        await callback.message.edit_text(reply.text, reply_markup=reply.reply_markup)
        await callback.answer()

    @router.callback_query(F.data.startswith(f"{PROFILE_CALLBACK_PREFIX}:"))
    async def profile_callback_handler(
        callback: CallbackQuery,
        partner: Partner,
        state: FSMContext,
    ) -> None:
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
            await state.clear()
            reply = await sync_to_async(_invalid_profile_action_reply)(
                partner=partner,
                language=profile.language,
            )
        else:
            if callback.data == f"{PROFILE_CALLBACK_PREFIX}:contact":
                await state.set_state(ClientMessageState.waiting_for_text)
            else:
                await state.clear()

        await callback.message.edit_text(reply.text, reply_markup=reply.reply_markup)
        await callback.answer()

    @router.callback_query(F.data.startswith(f"{MASTER_MESSAGE_CALLBACK_PREFIX}:"))
    async def master_message_callback_handler(
        callback: CallbackQuery,
        partner: Partner,
        state: FSMContext,
    ) -> None:
        contact = await sync_to_async(master_telegram_contact_for_user)(
            partner=partner,
            telegram_user_id=callback.from_user.id,
        )
        if contact is None or contact.user is None:
            rendered = await sync_to_async(render_content_template)(
                partner=partner,
                key="error.invalid_action",
                language=partner.default_language,
            )
            await callback.answer(rendered.body[:200], show_alert=True)
            return
        if callback.message is None or callback.data is None:
            await callback.answer()
            return

        try:
            result = await sync_to_async(_master_message_result_from_callback_data)(
                partner=partner,
                actor=contact.user,
                data=callback.data,
            )
        except ValidationError:
            await state.clear()
            rendered = await sync_to_async(render_content_template)(
                partner=partner,
                key="error.invalid_action",
                language=partner.default_language,
            )
            await callback.answer(rendered.body[:200], show_alert=True)
            return

        if result.wait_for_text and result.source_message_id is not None:
            await _store_master_message_state(
                state=state,
                source_message_id=result.source_message_id,
            )
            await callback.message.answer(
                result.reply.text,
                reply_markup=result.reply.reply_markup,
            )
        else:
            await state.clear()
            await callback.message.edit_text(
                result.reply.text,
                reply_markup=result.reply.reply_markup,
            )
        await callback.answer()

    @router.callback_query(F.data.startswith(f"{FAILED_NOTIFICATION_CALLBACK_PREFIX}:"))
    async def failed_notification_callback_handler(
        callback: CallbackQuery,
        partner: Partner,
        state: FSMContext,
    ) -> None:
        contact = await sync_to_async(master_telegram_contact_for_user)(
            partner=partner,
            telegram_user_id=callback.from_user.id,
        )
        if contact is None or contact.user is None:
            rendered = await sync_to_async(render_content_template)(
                partner=partner,
                key="error.invalid_action",
                language=partner.default_language,
            )
            await callback.answer(rendered.body[:200], show_alert=True)
            return
        if callback.message is None or callback.data is None:
            await callback.answer()
            return

        try:
            result = await sync_to_async(_failed_notification_result_from_callback_data)(
                partner=partner,
                actor=contact.user,
                data=callback.data,
                deliver=deliver_scheduled_notification,
            )
        except ValidationError:
            await state.clear()
            rendered = await sync_to_async(render_content_template)(
                partner=partner,
                key="error.invalid_action",
                language=partner.default_language,
            )
            await callback.answer(rendered.body[:200], show_alert=True)
            return

        if result.wait_for_text and result.source_notification_id is not None:
            await _store_failed_notification_state(
                state=state,
                source_notification_id=result.source_notification_id,
            )
            await callback.message.answer(
                result.reply.text,
                reply_markup=result.reply.reply_markup,
            )
        elif result.send_new_message:
            await state.clear()
            await callback.message.answer(
                result.reply.text,
                reply_markup=result.reply.reply_markup,
            )
        else:
            await state.clear()
            await callback.message.edit_text(
                result.reply.text,
                reply_markup=result.reply.reply_markup,
            )
        await callback.answer()

    @router.callback_query(F.data.startswith(f"{MASTER_REQUEST_CALLBACK_PREFIX}:"))
    async def master_request_callback_handler(
        callback: CallbackQuery,
        partner: Partner,
        state: FSMContext,
    ) -> None:
        if callback.data == f"{MASTER_REQUEST_CALLBACK_PREFIX}:noop":
            await callback.answer()
            return

        contact = await sync_to_async(master_telegram_contact_for_user)(
            partner=partner,
            telegram_user_id=callback.from_user.id,
        )
        if contact is None or contact.user is None:
            rendered = await sync_to_async(render_content_template)(
                partner=partner,
                key="error.invalid_action",
                language=partner.default_language,
            )
            await callback.answer(rendered.body[:200], show_alert=True)
            return
        if callback.message is None or callback.data is None:
            await callback.answer()
            return

        proposal_state = _master_proposal_state_values(await state.get_data())
        try:
            result = await sync_to_async(_master_callback_result_from_data)(
                partner=partner,
                actor=contact.user,
                data=callback.data,
                proposal_state=proposal_state,
            )
        except ValidationError:
            await state.clear()
            reply = await sync_to_async(build_master_booking_request_action_failed_reply)(
                partner=partner,
                appointment_id=_master_request_id_from_callback(callback.data),
            )
        else:
            reply = result.reply
            if result.keep_proposal_state and result.appointment_id is not None:
                await _store_master_proposal_state(
                    state=state,
                    appointment_id=result.appointment_id,
                    selected_start_ats=result.selected_start_ats,
                )
            else:
                await state.clear()

        await callback.message.edit_text(reply.text, reply_markup=reply.reply_markup)
        await callback.answer()

    return router


def _booking_reply_from_callback_data(
    *,
    partner: Partner,
    client_profile,
    data: str,
    selected_service_ids: tuple[UUID, ...] = (),
    summary_signature: str = "",
    client_comment: str = "",
) -> BookingCallbackResult:
    parts = data.split(":")
    if len(parts) < 2 or parts[0] != BOOKING_CALLBACK_PREFIX:
        raise ValidationError({"callback": "Unsupported callback."})

    action = parts[1]
    language = client_profile.language
    if action == "cat" and len(parts) == 3:
        return BookingCallbackResult(
            reply=build_booking_categories_reply(
                partner=partner,
                language=language,
                page=parse_page_token(parts[2]),
                selected_service_ids=selected_service_ids,
            ),
            selected_service_ids=selected_service_ids,
            summary_signature=summary_signature,
        )
    if action == "svc" and len(parts) == 4:
        return BookingCallbackResult(
            reply=build_booking_services_reply(
                partner=partner,
                category_id=parse_uuid_token(parts[2]),
                language=language,
                page=parse_page_token(parts[3]),
                selected_service_ids=selected_service_ids,
            ),
            selected_service_ids=selected_service_ids,
            summary_signature=summary_signature,
        )
    if action == "toggle" and len(parts) == 4:
        change = toggle_booking_service_selection(
            partner=partner,
            selected_service_ids=selected_service_ids,
            service_id=parse_uuid_token(parts[2]),
        )
        return BookingCallbackResult(
            reply=build_booking_services_reply(
                partner=partner,
                category_id=change.category_id,
                language=language,
                page=parse_page_token(parts[3]),
                selected_service_ids=change.selected_service_ids,
            ),
            selected_service_ids=change.selected_service_ids,
        )
    if action == "clear" and len(parts) == 4:
        category_id = parse_uuid_token(parts[2])
        return BookingCallbackResult(
            reply=build_booking_services_reply(
                partner=partner,
                category_id=category_id,
                language=language,
                page=parse_page_token(parts[3]),
            ),
            selected_service_ids=(),
            client_comment="",
        )
    if action == "clearcat" and len(parts) == 3:
        return BookingCallbackResult(
            reply=build_booking_categories_reply(
                partner=partner,
                language=language,
                page=parse_page_token(parts[2]),
            ),
            selected_service_ids=(),
            client_comment="",
        )
    if action == "restart" and len(parts) == 3:
        return BookingCallbackResult(
            reply=build_booking_categories_reply(
                partner=partner,
                language=language,
                page=parse_page_token(parts[2]),
            ),
            selected_service_ids=(),
            client_comment="",
        )
    if action == "sum" and len(parts) == 3:
        current_signature = booking_selection_signature(
            partner=partner,
            selected_service_ids=selected_service_ids,
        )
        selection_changed = bool(summary_signature and current_signature != summary_signature)
        return BookingCallbackResult(
            reply=build_booking_summary_reply(
                partner=partner,
                selected_service_ids=selected_service_ids,
                language=language,
                page=parse_page_token(parts[2]),
                selection_changed=selection_changed,
            ),
            selected_service_ids=selected_service_ids,
            summary_signature=current_signature,
        )
    if action == "remove" and len(parts) == 4:
        selection_changed = False
        if summary_signature:
            selection_changed = (
                booking_selection_signature(
                    partner=partner,
                    selected_service_ids=selected_service_ids,
                )
                != summary_signature
            )
        updated_ids = remove_booking_service_selection(
            selected_service_ids=selected_service_ids,
            service_id=parse_uuid_token(parts[2]),
        )
        if not updated_ids:
            return BookingCallbackResult(
                reply=build_booking_categories_reply(partner=partner, language=language),
                selected_service_ids=(),
                client_comment="",
            )
        current_signature = booking_selection_signature(
            partner=partner,
            selected_service_ids=updated_ids,
        )
        return BookingCallbackResult(
            reply=build_booking_summary_reply(
                partner=partner,
                selected_service_ids=updated_ids,
                language=language,
                page=parse_page_token(parts[3]),
                selection_changed=selection_changed,
            ),
            selected_service_ids=updated_ids,
            summary_signature=current_signature,
        )

    if action in {
        "dates",
        "months",
        "month",
        "slots",
        "final",
        "comment",
        "uncomment",
        "create",
    }:
        review_result = _booking_selection_review_result(
            partner=partner,
            language=language,
            selected_service_ids=selected_service_ids,
            summary_signature=summary_signature,
        )
        if review_result is not None:
            return review_result

    if action == "dates" and len(parts) == 3:
        return BookingCallbackResult(
            reply=build_booking_dates_reply(
                partner=partner,
                selected_service_ids=selected_service_ids,
                language=language,
                page=parse_page_token(parts[2]),
            ),
            selected_service_ids=selected_service_ids,
            summary_signature=summary_signature,
        )
    if action == "months" and len(parts) == 3:
        return BookingCallbackResult(
            reply=build_booking_months_reply(
                partner=partner,
                selected_service_ids=selected_service_ids,
                language=language,
                page=parse_page_token(parts[2]),
            ),
            selected_service_ids=selected_service_ids,
            summary_signature=summary_signature,
        )
    if action == "month" and len(parts) == 4:
        return BookingCallbackResult(
            reply=build_booking_month_dates_reply(
                partner=partner,
                selected_service_ids=selected_service_ids,
                month_start=parse_month_token(parts[2]),
                language=language,
                page=parse_page_token(parts[3]),
            ),
            selected_service_ids=selected_service_ids,
            summary_signature=summary_signature,
        )
    if action == "slots" and len(parts) == 5:
        return BookingCallbackResult(
            reply=build_booking_slots_reply(
                partner=partner,
                selected_service_ids=selected_service_ids,
                local_date=parse_date_token(parts[2]),
                language=language,
                page=parse_page_token(parts[3]),
                date_source=parts[4],
            ),
            selected_service_ids=selected_service_ids,
            summary_signature=summary_signature,
        )
    if action == "final" and len(parts) == 4:
        return BookingCallbackResult(
            reply=build_booking_final_confirm_reply(
                partner=partner,
                selected_service_ids=selected_service_ids,
                start_at=parse_timestamp_token(parts[2]),
                language=language,
                date_source=parts[3],
                client_comment=client_comment,
            ),
            selected_service_ids=selected_service_ids,
            summary_signature=summary_signature,
            client_comment=client_comment,
        )
    if action == "comment" and len(parts) == 4:
        start_at = parse_timestamp_token(parts[2])
        date_source = parts[3]
        return BookingCallbackResult(
            reply=build_booking_comment_prompt_reply(
                partner=partner,
                start_at=start_at,
                language=language,
                date_source=date_source,
                client_comment=client_comment,
            ),
            selected_service_ids=selected_service_ids,
            summary_signature=summary_signature,
            input_mode=BOOKING_INPUT_COMMENT,
            pending_start_at=start_at,
            date_source=date_source,
        )
    if action == "uncomment" and len(parts) == 4:
        start_at = parse_timestamp_token(parts[2])
        date_source = parts[3]
        return BookingCallbackResult(
            reply=build_booking_final_confirm_reply(
                partner=partner,
                selected_service_ids=selected_service_ids,
                start_at=start_at,
                language=language,
                date_source=date_source,
            ),
            selected_service_ids=selected_service_ids,
            summary_signature=summary_signature,
            client_comment="",
        )
    if action == "create" and len(parts) == 4:
        start_at = parse_timestamp_token(parts[2])
        date_source = parts[3]
        validate_date_source_token(
            date_source=date_source,
            local_date=to_partner_time(start_at, partner.timezone).date(),
        )
        if booking_requires_phone(partner=partner, client_profile=client_profile):
            return BookingCallbackResult(
                reply=build_booking_required_phone_reply(
                    partner=partner,
                    start_at=start_at,
                    language=language,
                    date_source=date_source,
                ),
                selected_service_ids=selected_service_ids,
                summary_signature=summary_signature,
                input_mode=BOOKING_INPUT_PHONE,
                pending_start_at=start_at,
                date_source=date_source,
            )
        try:
            reply = confirm_booking_reply(
                partner=partner,
                client_profile=client_profile,
                selected_service_ids=selected_service_ids,
                start_at=start_at,
                language=language,
                client_comment=client_comment,
            )
        except ValidationError as exc:
            if "start_at" not in getattr(exc, "message_dict", {}):
                raise
            return BookingCallbackResult(
                reply=build_booking_slot_taken_reply(
                    partner=partner,
                    start_at=start_at,
                    language=language,
                    date_source=date_source,
                ),
                selected_service_ids=selected_service_ids,
                summary_signature=summary_signature,
            )
        return BookingCallbackResult(
            reply=reply,
            selected_service_ids=selected_service_ids,
            summary_signature=summary_signature,
            complete=True,
        )

    raise ValidationError({"callback": "Unsupported callback."})


def _booking_selection_review_result(
    *,
    partner: Partner,
    language: str,
    selected_service_ids: tuple[UUID, ...],
    summary_signature: str,
) -> BookingCallbackResult | None:
    current_signature = booking_selection_signature(
        partner=partner,
        selected_service_ids=selected_service_ids,
    )
    if current_signature == summary_signature:
        return None
    return BookingCallbackResult(
        reply=build_booking_summary_reply(
            partner=partner,
            selected_service_ids=selected_service_ids,
            language=language,
            selection_changed=True,
        ),
        selected_service_ids=selected_service_ids,
        summary_signature=current_signature,
    )


def _booking_reply_from_comment_input(
    *,
    partner: Partner,
    client_profile,
    booking_state: BookingStateValues,
    text: str,
) -> BookingCallbackResult:
    """Return the refreshed final confirmation after validating a comment message."""

    comment = normalize_booking_comment(text)
    result = _booking_reply_from_callback_data(
        partner=partner,
        client_profile=client_profile,
        data=_pending_booking_callback("final", booking_state),
        selected_service_ids=booking_state.selected_service_ids,
        summary_signature=booking_state.summary_signature,
        client_comment=comment,
    )
    return result


def _booking_reply_from_phone_input(
    *,
    partner: Partner,
    client_profile,
    booking_state: BookingStateValues,
    phone: str,
) -> BookingCallbackResult:
    """Save a client-supplied phone, then resume the already-confirmed booking."""

    updated_profile = update_client_profile_phone_from_telegram(
        client_profile=client_profile,
        phone=phone,
    )
    return _booking_reply_from_callback_data(
        partner=partner,
        client_profile=updated_profile,
        data=_pending_booking_callback("create", booking_state),
        selected_service_ids=booking_state.selected_service_ids,
        summary_signature=booking_state.summary_signature,
        client_comment=booking_state.client_comment,
    )


def _pending_booking_callback(action: str, booking_state: BookingStateValues) -> str:
    if booking_state.pending_start_at is None or not booking_state.date_source:
        raise ValidationError({"callback": "Pending booking input has expired."})
    timestamp = int(booking_state.pending_start_at.timestamp())
    return f"{BOOKING_CALLBACK_PREFIX}:{action}:{timestamp}:{booking_state.date_source}"


def _invalid_booking_action_reply(*, partner: Partner, language: str):
    rendered = render_content_template(
        partner=partner,
        key="booking.selection_changed",
        language=language,
    )
    categories = build_booking_categories_reply(partner=partner, language=language)
    return BotBookingReply(
        text=f"{rendered.body}\n\n{categories.text}",
        reply_markup=categories.reply_markup,
    )


def _profile_reply_from_callback_data(*, partner: Partner, client_profile, data: str):
    parts = data.split(":")
    if len(parts) < 2 or parts[0] != PROFILE_CALLBACK_PREFIX:
        raise ValidationError({"callback": "Unsupported callback."})

    action = parts[1]
    if action == "main" and len(parts) == 2:
        return build_profile_main_reply(partner=partner, client_profile=client_profile)
    if action == "contact" and len(parts) == 2:
        return build_contact_master_prompt_reply(
            partner=partner,
            client_profile=client_profile,
        )
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
    if action == "rsd" and len(parts) == 5:
        return build_reschedule_dates_reply(
            partner=partner,
            client_profile=client_profile,
            appointment_id=parse_profile_uuid_token(parts[2]),
            return_page=parse_profile_page_token(parts[3]),
            page=parse_profile_page_token(parts[4]),
        )
    if action == "rss" and len(parts) == 6:
        return build_reschedule_slots_reply(
            partner=partner,
            client_profile=client_profile,
            appointment_id=parse_profile_uuid_token(parts[2]),
            local_date=parse_date_token(parts[3]),
            return_page=parse_profile_page_token(parts[4]),
            page=parse_profile_page_token(parts[5]),
        )
    if action == "rsf" and len(parts) == 5:
        return build_reschedule_final_confirm_reply(
            partner=partner,
            client_profile=client_profile,
            appointment_id=parse_profile_uuid_token(parts[2]),
            start_at=parse_timestamp_token(parts[3]),
            return_page=parse_profile_page_token(parts[4]),
        )
    if action == "rsg" and len(parts) == 5:
        return confirm_reschedule_reply(
            partner=partner,
            client_profile=client_profile,
            appointment_id=parse_profile_uuid_token(parts[2]),
            start_at=parse_timestamp_token(parts[3]),
            return_page=parse_profile_page_token(parts[4]),
        )
    if action == "pad" and len(parts) == 6:
        return build_requested_alternative_dates_reply(
            partner=partner,
            client_profile=client_profile,
            proposal_id=parse_profile_uuid_token(parts[2]),
            return_action=parse_return_action_token(parts[3]),
            return_page=parse_profile_page_token(parts[4]),
            page=parse_profile_page_token(parts[5]),
        )
    if action == "pas" and len(parts) == 7:
        return build_requested_alternative_slots_reply(
            partner=partner,
            client_profile=client_profile,
            proposal_id=parse_profile_uuid_token(parts[2]),
            local_date=parse_date_token(parts[3]),
            return_action=parse_return_action_token(parts[4]),
            return_page=parse_profile_page_token(parts[5]),
            page=parse_profile_page_token(parts[6]),
        )
    if action == "paf" and len(parts) == 6:
        return build_requested_alternative_final_confirm_reply(
            partner=partner,
            client_profile=client_profile,
            proposal_id=parse_profile_uuid_token(parts[2]),
            start_at=parse_timestamp_token(parts[3]),
            return_action=parse_return_action_token(parts[4]),
            return_page=parse_profile_page_token(parts[5]),
        )
    if action == "pag" and len(parts) == 6:
        return confirm_requested_alternative_reply(
            partner=partner,
            client_profile=client_profile,
            proposal_id=parse_profile_uuid_token(parts[2]),
            start_at=parse_timestamp_token(parts[3]),
            return_action=parse_return_action_token(parts[4]),
            return_page=parse_profile_page_token(parts[5]),
        )
    if action == "proposal" and len(parts) == 5:
        return_action = parts[3]
        _validate_profile_return_action(return_action)
        return build_appointment_proposal_reply(
            partner=partner,
            client_profile=client_profile,
            proposal_id=parse_profile_uuid_token(parts[2]),
            return_action=return_action,
            return_page=parse_profile_page_token(parts[4]),
        )
    if action == "proposalgo" and len(parts) == 5:
        return_action = parts[3]
        _validate_profile_return_action(return_action)
        return accept_appointment_proposal_reply(
            partner=partner,
            client_profile=client_profile,
            option_id=parse_profile_integer_token(parts[2]),
            return_action=return_action,
            return_page=parse_profile_page_token(parts[4]),
        )
    if action == "proposalno" and len(parts) == 5:
        return_action = parts[3]
        _validate_profile_return_action(return_action)
        return decline_appointment_proposal_reply(
            partner=partner,
            client_profile=client_profile,
            proposal_id=parse_profile_uuid_token(parts[2]),
            return_action=return_action,
            return_page=parse_profile_page_token(parts[4]),
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


def _master_callback_result_from_data(
    *,
    partner: Partner,
    actor,
    data: str,
    proposal_state: MasterProposalStateValues | None = None,
) -> MasterCallbackResult:
    parts = data.split(":")
    if len(parts) < 3 or parts[0] != MASTER_REQUEST_CALLBACK_PREFIX:
        raise ValidationError({"callback": "Unsupported callback."})

    appointment_id = parse_master_request_appointment_id(parts[2])
    proposal_state = proposal_state or MasterProposalStateValues()
    selected_start_ats = (
        proposal_state.selected_start_ats if proposal_state.appointment_id == appointment_id else ()
    )
    action = parts[1]
    if action == "view" and len(parts) == 3:
        return MasterCallbackResult(
            reply=build_master_booking_request_reply(
                partner=partner,
                appointment_id=appointment_id,
            )
        )
    if action == "confirm" and len(parts) == 3:
        return MasterCallbackResult(
            reply=confirm_master_booking_request_reply(
                partner=partner,
                actor=actor,
                appointment_id=appointment_id,
            )
        )
    if action == "decline" and len(parts) == 3:
        return MasterCallbackResult(
            reply=build_master_booking_request_decline_reply(
                partner=partner,
                appointment_id=appointment_id,
            )
        )
    if action == "declinego" and len(parts) == 3:
        return MasterCallbackResult(
            reply=decline_master_booking_request_reply(
                partner=partner,
                actor=actor,
                appointment_id=appointment_id,
            )
        )
    if action == "propose" and len(parts) == 3:
        return MasterCallbackResult(
            reply=build_master_requested_alternative_dates_reply(
                partner=partner,
                actor=actor,
                appointment_id=appointment_id,
            ),
            appointment_id=appointment_id,
            keep_proposal_state=True,
        )
    if action == "pdates" and len(parts) == 4:
        return MasterCallbackResult(
            reply=build_master_requested_alternative_dates_reply(
                partner=partner,
                actor=actor,
                appointment_id=appointment_id,
                selected_start_ats=selected_start_ats,
                page=parse_master_proposal_page_token(parts[3]),
            ),
            appointment_id=appointment_id,
            selected_start_ats=selected_start_ats,
            keep_proposal_state=True,
        )
    if action == "pslots" and len(parts) == 5:
        return MasterCallbackResult(
            reply=build_master_requested_alternative_slots_reply(
                partner=partner,
                actor=actor,
                appointment_id=appointment_id,
                local_date=parse_master_proposal_date_token(parts[3]),
                selected_start_ats=selected_start_ats,
                page=parse_master_proposal_page_token(parts[4]),
            ),
            appointment_id=appointment_id,
            selected_start_ats=selected_start_ats,
            keep_proposal_state=True,
        )
    if action == "ptoggle" and len(parts) == 5:
        selection = toggle_master_requested_alternative_slot_reply(
            partner=partner,
            actor=actor,
            appointment_id=appointment_id,
            start_at=parse_master_proposal_timestamp_token(parts[3]),
            selected_start_ats=selected_start_ats,
            page=parse_master_proposal_page_token(parts[4]),
        )
        return MasterCallbackResult(
            reply=selection.reply,
            appointment_id=appointment_id,
            selected_start_ats=selection.selected_start_ats,
            keep_proposal_state=True,
        )
    if action == "preview" and len(parts) == 3:
        return MasterCallbackResult(
            reply=build_master_requested_alternative_review_reply(
                partner=partner,
                actor=actor,
                appointment_id=appointment_id,
                selected_start_ats=selected_start_ats,
            ),
            appointment_id=appointment_id,
            selected_start_ats=selected_start_ats,
            keep_proposal_state=True,
        )
    if action == "psend" and len(parts) == 3:
        return MasterCallbackResult(
            reply=send_master_requested_alternative_proposal_reply(
                partner=partner,
                actor=actor,
                appointment_id=appointment_id,
                selected_start_ats=selected_start_ats,
            )
        )
    raise ValidationError({"callback": "Unsupported callback."})


def _master_reply_from_callback_data(*, partner: Partner, actor, data: str) -> BotMasterReply:
    """Compatibility helper for stateless master actions and focused adapter tests."""

    return _master_callback_result_from_data(
        partner=partner,
        actor=actor,
        data=data,
    ).reply


def _master_message_result_from_callback_data(
    *,
    partner: Partner,
    actor,
    data: str,
) -> MasterMessageCallbackResult:
    """Resolve one master messaging callback inside trusted partner scope."""

    parts = data.split(":")
    if len(parts) != 3 or parts[0] != MASTER_MESSAGE_CALLBACK_PREFIX:
        raise ValidationError({"callback": "Unsupported callback."})
    source_message_id = parse_master_message_id(parts[2])
    if parts[1] == "reply":
        return MasterMessageCallbackResult(
            reply=build_master_conversation_reply_prompt(
                partner=partner,
                actor=actor,
                source_message_id=source_message_id,
            ),
            source_message_id=source_message_id,
            wait_for_text=True,
        )
    if parts[1] == "cancel":
        return MasterMessageCallbackResult(
            reply=cancel_master_conversation_reply(
                partner=partner,
                actor=actor,
                source_message_id=source_message_id,
            )
        )
    raise ValidationError({"callback": "Unsupported callback."})


def _failed_notification_result_from_callback_data(
    *,
    partner: Partner,
    actor,
    data: str,
    deliver=deliver_scheduled_notification,
) -> FailedNotificationCallbackResult:
    """Resolve one failed-reminder action inside trusted partner scope."""

    parts = data.split(":")
    if len(parts) != 3 or parts[0] != FAILED_NOTIFICATION_CALLBACK_PREFIX:
        raise ValidationError({"callback": "Unsupported callback."})
    source_notification_id = parse_failed_notification_id(parts[2])
    action = parts[1]
    if action == "retry":
        return FailedNotificationCallbackResult(
            reply=retry_failed_client_reminder_reply(
                partner=partner,
                actor=actor,
                source_notification_id=source_notification_id,
                deliver=deliver,
            )
        )
    if action == "client":
        return FailedNotificationCallbackResult(
            reply=build_failed_client_summary_reply(
                partner=partner,
                actor=actor,
                source_notification_id=source_notification_id,
            ),
            send_new_message=True,
        )
    if action == "message":
        reply = build_failed_client_message_prompt_reply(
            partner=partner,
            actor=actor,
            source_notification_id=source_notification_id,
        )
        return FailedNotificationCallbackResult(
            reply=reply,
            source_notification_id=(source_notification_id if reply.wait_for_text else None),
            wait_for_text=reply.wait_for_text,
            send_new_message=True,
        )
    if action == "cancel":
        return FailedNotificationCallbackResult(
            reply=cancel_failed_client_message_reply(
                partner=partner,
                actor=actor,
                source_notification_id=source_notification_id,
            )
        )
    raise ValidationError({"callback": "Unsupported callback."})


def _master_request_id_from_callback(data: str) -> UUID | None:
    parts = data.split(":")
    if len(parts) < 3 or parts[0] != MASTER_REQUEST_CALLBACK_PREFIX:
        return None
    try:
        return parse_master_request_appointment_id(parts[2])
    except ValidationError:
        return None


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


async def _store_booking_state(
    *,
    state: FSMContext,
    selected_service_ids: tuple[UUID, ...],
    summary_signature: str,
    client_comment: str = "",
    pending_start_at: datetime | None = None,
    date_source: str = "",
    next_state: State = BookingFlowState.active,
) -> None:
    await state.set_state(next_state)
    await state.set_data(
        {
            BOOKING_SERVICE_IDS_STATE_KEY: [service_id.hex for service_id in selected_service_ids],
            BOOKING_SUMMARY_SIGNATURE_STATE_KEY: summary_signature,
            BOOKING_COMMENT_STATE_KEY: client_comment,
            BOOKING_PENDING_START_STATE_KEY: (
                int(pending_start_at.timestamp()) if pending_start_at is not None else ""
            ),
            BOOKING_DATE_SOURCE_STATE_KEY: date_source,
        }
    )


async def _apply_booking_result_to_state(
    *,
    state: FSMContext,
    previous: BookingStateValues,
    result: BookingCallbackResult,
) -> None:
    if result.complete:
        await state.clear()
        return

    next_state = BookingFlowState.active
    if result.input_mode == BOOKING_INPUT_COMMENT:
        next_state = BookingFlowState.waiting_for_comment
    elif result.input_mode == BOOKING_INPUT_PHONE:
        next_state = BookingFlowState.waiting_for_phone

    client_comment = (
        previous.client_comment if result.client_comment is None else result.client_comment
    )
    await _store_booking_state(
        state=state,
        selected_service_ids=result.selected_service_ids,
        summary_signature=result.summary_signature,
        client_comment=client_comment,
        pending_start_at=result.pending_start_at,
        date_source=result.date_source,
        next_state=next_state,
    )


def _booking_state_values(data: dict) -> BookingStateValues:
    raw_ids = data.get(BOOKING_SERVICE_IDS_STATE_KEY, ())
    if not isinstance(raw_ids, (list, tuple)):
        return BookingStateValues()
    try:
        service_ids = tuple(UUID(str(value)) for value in raw_ids)
    except (TypeError, ValueError):
        return BookingStateValues()
    summary_signature = data.get(BOOKING_SUMMARY_SIGNATURE_STATE_KEY, "")
    if not isinstance(summary_signature, str):
        summary_signature = ""
    raw_comment = data.get(BOOKING_COMMENT_STATE_KEY, "")
    try:
        client_comment = normalize_booking_comment(raw_comment)
    except ValidationError:
        client_comment = ""

    pending_start_at = None
    raw_start = data.get(BOOKING_PENDING_START_STATE_KEY)
    if raw_start not in (None, ""):
        try:
            pending_start_at = parse_timestamp_token(str(raw_start))
        except ValidationError:
            pending_start_at = None

    date_source = data.get(BOOKING_DATE_SOURCE_STATE_KEY, "")
    if not isinstance(date_source, str):
        date_source = ""
    return BookingStateValues(
        selected_service_ids=service_ids,
        summary_signature=summary_signature,
        client_comment=client_comment,
        pending_start_at=pending_start_at,
        date_source=date_source,
    )


async def _store_master_proposal_state(
    *,
    state: FSMContext,
    appointment_id: UUID,
    selected_start_ats: tuple[datetime, ...],
) -> None:
    await state.set_state(MasterProposalFlowState.active)
    await state.set_data(
        {
            MASTER_PROPOSAL_APPOINTMENT_STATE_KEY: appointment_id.hex,
            MASTER_PROPOSAL_STARTS_STATE_KEY: [
                int(start_at.timestamp()) for start_at in selected_start_ats
            ],
        }
    )


async def _store_master_message_state(
    *,
    state: FSMContext,
    source_message_id: UUID,
) -> None:
    await state.set_state(MasterMessageFlowState.waiting_for_text)
    await state.set_data({MASTER_MESSAGE_SOURCE_STATE_KEY: source_message_id.hex})


async def _store_failed_notification_state(
    *,
    state: FSMContext,
    source_notification_id: UUID,
) -> None:
    await state.set_state(FailedNotificationMessageFlowState.waiting_for_text)
    await state.set_data({FAILED_NOTIFICATION_SOURCE_STATE_KEY: source_notification_id.hex})


def _master_message_state_id(data: dict) -> UUID | None:
    raw_message_id = data.get(MASTER_MESSAGE_SOURCE_STATE_KEY)
    if not raw_message_id:
        return None
    try:
        return parse_master_message_id(str(raw_message_id))
    except ValidationError:
        return None


def _failed_notification_state_id(data: dict) -> UUID | None:
    raw_notification_id = data.get(FAILED_NOTIFICATION_SOURCE_STATE_KEY)
    if not raw_notification_id:
        return None
    try:
        return parse_failed_notification_id(str(raw_notification_id))
    except ValidationError:
        return None


def _invalid_master_message_action_reply(*, partner: Partner) -> BotConversationReply:
    rendered = render_content_template(
        partner=partner,
        key="error.invalid_action",
        language=partner.default_language,
    )
    return BotConversationReply(text=rendered.body)


def _invalid_failed_notification_action_reply(*, partner: Partner) -> BotFailedDeliveryReply:
    rendered = render_content_template(
        partner=partner,
        key="error.invalid_action",
        language=partner.default_language,
    )
    return BotFailedDeliveryReply(text=rendered.body)


def _master_proposal_state_values(data: dict) -> MasterProposalStateValues:
    raw_appointment_id = data.get(MASTER_PROPOSAL_APPOINTMENT_STATE_KEY)
    try:
        appointment_id = (
            parse_master_request_appointment_id(str(raw_appointment_id))
            if raw_appointment_id
            else None
        )
    except ValidationError:
        return MasterProposalStateValues()

    raw_start_ats = data.get(MASTER_PROPOSAL_STARTS_STATE_KEY, ())
    if not isinstance(raw_start_ats, (list, tuple)):
        return MasterProposalStateValues(appointment_id=appointment_id)
    try:
        selected_start_ats = tuple(
            parse_master_proposal_timestamp_token(str(value)) for value in raw_start_ats
        )
    except ValidationError:
        return MasterProposalStateValues(appointment_id=appointment_id)
    return MasterProposalStateValues(
        appointment_id=appointment_id,
        selected_start_ats=selected_start_ats,
    )


def _phone_from_message(message: Message) -> str | None:
    contact = message.contact
    if contact is not None:
        if contact.user_id is not None and (
            message.from_user is None or contact.user_id != message.from_user.id
        ):
            return None
        return contact.phone_number
    return message.text


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
