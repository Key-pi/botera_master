from __future__ import annotations

from dataclasses import dataclass
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
    booking_selection_signature,
    build_booking_categories_reply,
    build_booking_dates_reply,
    build_booking_final_confirm_reply,
    build_booking_month_dates_reply,
    build_booking_months_reply,
    build_booking_services_reply,
    build_booking_slot_taken_reply,
    build_booking_slots_reply,
    build_booking_summary_reply,
    confirm_booking_reply,
    parse_date_token,
    parse_month_token,
    parse_page_token,
    parse_timestamp_token,
    parse_uuid_token,
    remove_booking_service_selection,
    toggle_booking_service_selection,
    validate_date_source_token,
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
    build_reschedule_dates_reply,
    build_reschedule_final_confirm_reply,
    build_reschedule_slots_reply,
    confirm_cancel_appointment_reply,
    confirm_reschedule_reply,
    decline_appointment_proposal_reply,
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
from apps.bot.services import (
    handle_start_command,
    render_content_template,
    render_invalid_master_contact_link_message,
    resolve_client_profile_from_telegram_user,
)
from apps.partners.models import Partner
from core.timezones import to_partner_time


class ClientMessageState(StatesGroup):
    """Ephemeral input state before a durable conversation message exists."""

    waiting_for_text = State()


class BookingFlowState(StatesGroup):
    """Ephemeral multi-service selection for one active booking conversation."""

    active = State()


@dataclass(frozen=True, slots=True)
class BookingCallbackResult:
    """Booking adapter response plus the next ephemeral FSM state."""

    reply: BotBookingReply
    selected_service_ids: tuple[UUID, ...]
    summary_signature: str = ""
    complete: bool = False


BOOKING_SERVICE_IDS_STATE_KEY = "booking_service_ids"
BOOKING_SUMMARY_SIGNATURE_STATE_KEY = "booking_summary_signature"


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

        selected_service_ids, summary_signature = _booking_state_values(await state.get_data())
        try:
            result = await sync_to_async(_booking_reply_from_callback_data)(
                partner=partner,
                client_profile=profile,
                data=callback.data,
                selected_service_ids=selected_service_ids,
                summary_signature=summary_signature,
            )
        except ValidationError:
            await state.clear()
            reply = await sync_to_async(_invalid_booking_action_reply)(
                partner=partner,
                language=profile.language,
            )
        else:
            reply = result.reply
            if result.complete:
                await state.clear()
            else:
                await _store_booking_state(
                    state=state,
                    selected_service_ids=result.selected_service_ids,
                    summary_signature=result.summary_signature,
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

    return router


def _booking_reply_from_callback_data(
    *,
    partner: Partner,
    client_profile,
    data: str,
    selected_service_ids: tuple[UUID, ...] = (),
    summary_signature: str = "",
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
        )
    if action == "clearcat" and len(parts) == 3:
        return BookingCallbackResult(
            reply=build_booking_categories_reply(
                partner=partner,
                language=language,
                page=parse_page_token(parts[2]),
            ),
            selected_service_ids=(),
        )
    if action == "restart" and len(parts) == 3:
        return BookingCallbackResult(
            reply=build_booking_categories_reply(
                partner=partner,
                language=language,
                page=parse_page_token(parts[2]),
            ),
            selected_service_ids=(),
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

    if action in {"dates", "months", "month", "slots", "final", "create"}:
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
            ),
            selected_service_ids=selected_service_ids,
            summary_signature=summary_signature,
        )
    if action == "create" and len(parts) == 4:
        start_at = parse_timestamp_token(parts[2])
        date_source = parts[3]
        validate_date_source_token(
            date_source=date_source,
            local_date=to_partner_time(start_at, partner.timezone).date(),
        )
        try:
            reply = confirm_booking_reply(
                partner=partner,
                client_profile=client_profile,
                selected_service_ids=selected_service_ids,
                start_at=start_at,
                language=language,
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
) -> None:
    await state.set_state(BookingFlowState.active)
    await state.set_data(
        {
            BOOKING_SERVICE_IDS_STATE_KEY: [service_id.hex for service_id in selected_service_ids],
            BOOKING_SUMMARY_SIGNATURE_STATE_KEY: summary_signature,
        }
    )


def _booking_state_values(data: dict) -> tuple[tuple[UUID, ...], str]:
    raw_ids = data.get(BOOKING_SERVICE_IDS_STATE_KEY, ())
    if not isinstance(raw_ids, (list, tuple)):
        return (), ""
    try:
        service_ids = tuple(UUID(str(value)) for value in raw_ids)
    except (TypeError, ValueError):
        return (), ""
    summary_signature = data.get(BOOKING_SUMMARY_SIGNATURE_STATE_KEY, "")
    if not isinstance(summary_signature, str):
        summary_signature = ""
    return service_ids, summary_signature


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
