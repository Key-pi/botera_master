from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.appointments.models import Appointment
from apps.bot.booking import (
    MAX_BOOKING_COMMENT_LENGTH,
    booking_selection_signature,
    build_booking_categories_reply,
    build_booking_dates_reply,
    build_booking_final_confirm_reply,
    build_booking_month_dates_reply,
    build_booking_months_reply,
    build_booking_services_reply,
    build_booking_slots_reply,
    build_booking_summary_reply,
    confirm_booking_reply,
    normalize_booking_comment,
)
from apps.bot.routers import (
    BOOKING_INPUT_COMMENT,
    BOOKING_INPUT_PHONE,
    BookingStateValues,
    _booking_reply_from_callback_data,
    _booking_reply_from_comment_input,
    _booking_reply_from_phone_input,
)
from apps.catalog.services import create_service, create_service_category
from apps.clients.services import (
    resolve_client_profile_for_telegram_identity,
    upsert_telegram_identity,
)
from apps.partners.services import create_partner_with_owner
from apps.schedule.models import PartnerScheduleSettings
from apps.schedule.selectors import default_booking_resource_for_partner
from apps.schedule.services import create_availability_window, update_schedule_settings


@pytest.fixture
def bot_booking_context():
    superuser = User.objects.create_superuser(username="bot-booking-root", password="secret")
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Bot Booking Partner",
        owner_username="bot-booking-owner",
        owner_password="secret",
    )
    category = create_service_category(
        actor=created.owner,
        partner=created.partner,
        name="Nails",
    )
    service = create_service(
        actor=created.owner,
        partner=created.partner,
        category=category,
        name="Manicure",
        price_amount=50000,
        duration_minutes=60,
    )
    second_category = create_service_category(
        actor=created.owner,
        partner=created.partner,
        name="Brows",
    )
    second_service = create_service(
        actor=created.owner,
        partner=created.partner,
        category=second_category,
        name="Brow styling",
        price_amount=20_000,
        duration_minutes=30,
    )
    identity = upsert_telegram_identity(
        telegram_user_id=777001,
        username="client",
        first_name="Ira",
        language_code="en",
    )
    client = resolve_client_profile_for_telegram_identity(
        partner=created.partner,
        telegram_identity=identity,
    )
    return {
        "partner": created.partner,
        "owner": created.owner,
        "resource": default_booking_resource_for_partner(created.partner),
        "category": category,
        "service": service,
        "second_category": second_category,
        "second_service": second_service,
        "client": client,
    }


@pytest.fixture
def bot_booking_start():
    return (timezone.now() + timedelta(days=4)).replace(
        hour=9,
        minute=0,
        second=0,
        microsecond=0,
    )


def _open_window(context, start_at, *, hours=2):
    create_availability_window(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        start_at=start_at,
        end_at=start_at + timedelta(hours=hours),
    )


def _first_callback(reply):
    return reply.reply_markup.inline_keyboard[0][0].callback_data


def _callback_data(reply):
    if reply.reply_markup is None:
        return []
    return [
        button.callback_data
        for row in reply.reply_markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


@pytest.mark.django_db
def test_bot_booking_reply_builders_walk_multi_service_flow(
    bot_booking_context,
    bot_booking_start,
):
    context = bot_booking_context
    update_schedule_settings(
        actor=context["owner"],
        partner=context["partner"],
        confirmation_mode=PartnerScheduleSettings.ConfirmationMode.AUTO,
        slot_step_minutes=30,
    )
    _open_window(context, bot_booking_start)
    now = bot_booking_start - timedelta(hours=1)

    categories = build_booking_categories_reply(partner=context["partner"], language="en")
    services = build_booking_services_reply(
        partner=context["partner"],
        category_id=context["category"].id,
        language="en",
    )
    summary = build_booking_summary_reply(
        partner=context["partner"],
        selected_service_ids=[context["service"].id, context["second_service"].id],
        language="en",
    )
    dates = build_booking_dates_reply(
        partner=context["partner"],
        selected_service_ids=[context["service"].id, context["second_service"].id],
        language="en",
        now=now,
    )
    slots = build_booking_slots_reply(
        partner=context["partner"],
        selected_service_ids=[context["service"].id, context["second_service"].id],
        local_date=bot_booking_start.date(),
        language="en",
        now=now,
    )
    final = build_booking_final_confirm_reply(
        partner=context["partner"],
        selected_service_ids=[context["service"].id, context["second_service"].id],
        start_at=bot_booking_start,
        language="en",
        now=now,
    )

    assert "Choose a service category" in categories.text
    category_callbacks = {
        button.callback_data for row in categories.reply_markup.inline_keyboard for button in row
    }
    assert f"bk:svc:{context['category'].id.hex}:1" in category_callbacks
    assert f"bk:svc:{context['second_category'].id.hex}:1" in category_callbacks
    assert "Manicure" in services.reply_markup.inline_keyboard[0][0].text
    assert _first_callback(services) == f"bk:toggle:{context['service'].id.hex}:1"
    assert "Your selection" in summary.text
    assert "Manicure" in summary.text
    assert "Brow styling" in summary.text
    assert summary.reply_markup.inline_keyboard[-3][0].callback_data == "bk:dates:1"
    assert "Choose a date" in dates.text
    assert _first_callback(dates).startswith("bk:slots:")
    assert _first_callback(dates).endswith(":1:n")
    assert "Choose a time" in slots.text
    assert "Manicure" in slots.text
    assert "Brow styling" in slots.text
    assert _first_callback(slots).startswith("bk:final:")
    assert _first_callback(slots).endswith(":n")
    assert "Confirm booking" in final.text
    assert "90 min" in final.text
    assert _first_callback(final).startswith("bk:create:")
    assert all(
        len(callback_data.encode("utf-8")) <= 64
        for reply in (categories, services, summary, dates, slots, final)
        for callback_data in _callback_data(reply)
    )


@pytest.mark.django_db
def test_confirm_booking_reply_creates_confirmed_appointment(
    bot_booking_context,
    bot_booking_start,
):
    context = bot_booking_context
    update_schedule_settings(
        actor=context["owner"],
        partner=context["partner"],
        confirmation_mode=PartnerScheduleSettings.ConfirmationMode.AUTO,
    )
    _open_window(context, bot_booking_start)

    reply = confirm_booking_reply(
        partner=context["partner"],
        client_profile=context["client"],
        selected_service_ids=[context["service"].id, context["second_service"].id],
        start_at=bot_booking_start,
        language="en",
        now=bot_booking_start - timedelta(hours=1),
    )

    appointment = Appointment.objects.get(partner=context["partner"])
    assert appointment.status == Appointment.Status.CONFIRMED
    assert appointment.service_items.count() == 2
    assert appointment.end_at - appointment.start_at == timedelta(minutes=90)
    assert "confirmed" in reply.text


@pytest.mark.django_db
def test_bot_booking_adapter_rejects_cross_partner_service(bot_booking_context):
    context = bot_booking_context
    superuser = User.objects.create_superuser(username="other-root", password="secret")
    other = create_partner_with_owner(
        actor=superuser,
        partner_name="Other Partner",
        owner_username="other-owner",
        owner_password="secret",
    )
    other_category = create_service_category(
        actor=other.owner,
        partner=other.partner,
        name="Other",
    )
    other_service = create_service(
        actor=other.owner,
        partner=other.partner,
        category=other_category,
        name="Other service",
        price_amount=10000,
        duration_minutes=30,
    )

    with pytest.raises(ValidationError):
        build_booking_summary_reply(
            partner=context["partner"],
            selected_service_ids=[other_service.id],
            language="en",
        )


@pytest.mark.django_db
def test_booking_callback_selection_survives_categories_and_revalidates_summary(
    bot_booking_context,
):
    context = bot_booking_context
    first = _booking_reply_from_callback_data(
        partner=context["partner"],
        client_profile=context["client"],
        data=f"bk:toggle:{context['service'].id.hex}:1",
    )
    second = _booking_reply_from_callback_data(
        partner=context["partner"],
        client_profile=context["client"],
        data=f"bk:toggle:{context['second_service'].id.hex}:1",
        selected_service_ids=first.selected_service_ids,
    )
    summary = _booking_reply_from_callback_data(
        partner=context["partner"],
        client_profile=context["client"],
        data="bk:sum:1",
        selected_service_ids=second.selected_service_ids,
    )

    assert set(summary.selected_service_ids) == {
        context["service"].id,
        context["second_service"].id,
    }
    assert summary.summary_signature == booking_selection_signature(
        partner=context["partner"],
        selected_service_ids=summary.selected_service_ids,
    )

    context["second_service"].price_amount += 5_000
    context["second_service"].save(update_fields=["price_amount", "updated_at"])
    review = _booking_reply_from_callback_data(
        partner=context["partner"],
        client_profile=context["client"],
        data="bk:dates:1",
        selected_service_ids=summary.selected_service_ids,
        summary_signature=summary.summary_signature,
    )

    assert "changed" in review.reply.text
    assert "Your selection" in review.reply.text
    assert review.summary_signature != summary.summary_signature
    assert review.complete is False

    summary_review = _booking_reply_from_callback_data(
        partner=context["partner"],
        client_profile=context["client"],
        data="bk:sum:1",
        selected_service_ids=summary.selected_service_ids,
        summary_signature=summary.summary_signature,
    )

    assert "changed" in summary_review.reply.text
    assert summary_review.summary_signature == review.summary_signature


@pytest.mark.django_db
def test_booking_callback_can_remove_and_reset_selected_services(bot_booking_context):
    context = bot_booking_context
    selected_ids = (context["service"].id, context["second_service"].id)

    removed = _booking_reply_from_callback_data(
        partner=context["partner"],
        client_profile=context["client"],
        data=f"bk:remove:{context['second_service'].id.hex}:1",
        selected_service_ids=selected_ids,
    )

    assert removed.selected_service_ids == (context["service"].id,)
    assert "Manicure" in removed.reply.text
    assert "Brow styling" not in removed.reply.text

    reset = _booking_reply_from_callback_data(
        partner=context["partner"],
        client_profile=context["client"],
        data="bk:restart:1",
        selected_service_ids=removed.selected_service_ids,
        summary_signature=removed.summary_signature,
    )

    assert reset.selected_service_ids == ()
    assert reset.summary_signature == ""
    assert "Choose a service category" in reset.reply.text


@pytest.mark.django_db
def test_booking_month_navigation_reaches_far_horizon_and_preserves_back_path(
    bot_booking_context,
    bot_booking_start,
):
    context = bot_booking_context
    now = bot_booking_start - timedelta(hours=1)
    far_start = bot_booking_start + timedelta(days=90)
    month_token = far_start.strftime("%Y%m")
    selected_ids = (context["service"].id, context["second_service"].id)
    update_schedule_settings(
        actor=context["owner"],
        partner=context["partner"],
        confirmation_mode=PartnerScheduleSettings.ConfirmationMode.AUTO,
        booking_horizon_days=180,
    )
    _open_window(context, far_start)

    nearest_dates = build_booking_dates_reply(
        partner=context["partner"],
        selected_service_ids=selected_ids,
        language="en",
        now=now,
    )
    months = build_booking_months_reply(
        partner=context["partner"],
        selected_service_ids=selected_ids,
        language="en",
        now=now,
    )
    month_dates = build_booking_month_dates_reply(
        partner=context["partner"],
        selected_service_ids=selected_ids,
        month_start=far_start.date().replace(day=1),
        language="en",
        now=now,
    )
    slots = build_booking_slots_reply(
        partner=context["partner"],
        selected_service_ids=selected_ids,
        local_date=far_start.date(),
        language="en",
        date_source=month_token,
        now=now,
    )
    final = build_booking_final_confirm_reply(
        partner=context["partner"],
        selected_service_ids=selected_ids,
        start_at=far_start,
        language="en",
        date_source=month_token,
        now=now,
    )

    assert far_start.strftime("%Y%m%d") in _first_callback(nearest_dates)
    assert f"bk:month:{month_token}:1" in _callback_data(months)
    assert _first_callback(month_dates) == (
        f"bk:slots:{far_start.strftime('%Y%m%d')}:1:{month_token}"
    )
    assert slots.reply_markup.inline_keyboard[-1][0].callback_data == (f"bk:month:{month_token}:1")
    assert final.reply_markup.inline_keyboard[-1][0].callback_data == (
        f"bk:slots:{far_start.strftime('%Y%m%d')}:1:{month_token}"
    )
    assert all(
        len(callback_data.encode("utf-8")) <= 64
        for reply in (nearest_dates, months, month_dates, slots, final)
        for callback_data in _callback_data(reply)
    )

    with pytest.raises(ValidationError):
        build_booking_slots_reply(
            partner=context["partner"],
            selected_service_ids=selected_ids,
            local_date=far_start.date(),
            language="en",
            date_source="209901",
            now=now,
        )


@pytest.mark.django_db
def test_final_booking_race_returns_to_slots_without_clearing_selection(
    bot_booking_context,
    bot_booking_start,
):
    context = bot_booking_context
    selected_ids = (context["service"].id, context["second_service"].id)
    now = bot_booking_start - timedelta(hours=1)
    update_schedule_settings(
        actor=context["owner"],
        partner=context["partner"],
        confirmation_mode=PartnerScheduleSettings.ConfirmationMode.AUTO,
    )
    _open_window(context, bot_booking_start)
    final = build_booking_final_confirm_reply(
        partner=context["partner"],
        selected_service_ids=selected_ids,
        start_at=bot_booking_start,
        language="en",
        now=now,
    )
    confirm_booking_reply(
        partner=context["partner"],
        client_profile=context["client"],
        selected_service_ids=selected_ids,
        start_at=bot_booking_start,
        language="en",
        now=now,
    )

    result = _booking_reply_from_callback_data(
        partner=context["partner"],
        client_profile=context["client"],
        data=_first_callback(final),
        selected_service_ids=selected_ids,
        summary_signature=booking_selection_signature(
            partner=context["partner"],
            selected_service_ids=selected_ids,
        ),
    )

    assert result.complete is False
    assert result.selected_service_ids == selected_ids
    assert "already taken" in result.reply.text
    assert result.reply.reply_markup.inline_keyboard[0][0].callback_data == (
        f"bk:slots:{bot_booking_start.strftime('%Y%m%d')}:1:n"
    )


@pytest.mark.django_db
def test_booking_comment_is_collected_before_final_creation(
    bot_booking_context,
    bot_booking_start,
):
    context = bot_booking_context
    selected_ids = (context["service"].id, context["second_service"].id)
    signature = booking_selection_signature(
        partner=context["partner"],
        selected_service_ids=selected_ids,
    )
    update_schedule_settings(
        actor=context["owner"],
        partner=context["partner"],
        confirmation_mode=PartnerScheduleSettings.ConfirmationMode.AUTO,
    )
    _open_window(context, bot_booking_start)
    final = build_booking_final_confirm_reply(
        partner=context["partner"],
        selected_service_ids=selected_ids,
        start_at=bot_booking_start,
        language="en",
        now=bot_booking_start - timedelta(hours=1),
    )

    prompt = _booking_reply_from_callback_data(
        partner=context["partner"],
        client_profile=context["client"],
        data=final.reply_markup.inline_keyboard[1][0].callback_data,
        selected_service_ids=selected_ids,
        summary_signature=signature,
    )

    assert prompt.input_mode == BOOKING_INPUT_COMMENT
    assert prompt.pending_start_at == bot_booking_start
    assert "one text message" in prompt.reply.text

    reviewed = _booking_reply_from_comment_input(
        partner=context["partner"],
        client_profile=context["client"],
        booking_state=BookingStateValues(
            selected_service_ids=selected_ids,
            summary_signature=signature,
            pending_start_at=prompt.pending_start_at,
            date_source=prompt.date_source,
        ),
        text="  Please use a neutral color.  ",
    )

    assert reviewed.client_comment == "Please use a neutral color."
    assert "Comment: Please use a neutral color." in reviewed.reply.text

    created = _booking_reply_from_callback_data(
        partner=context["partner"],
        client_profile=context["client"],
        data=reviewed.reply.reply_markup.inline_keyboard[0][0].callback_data,
        selected_service_ids=selected_ids,
        summary_signature=signature,
        client_comment=reviewed.client_comment,
    )

    assert created.complete is True
    assert Appointment.objects.get(partner=context["partner"]).client_comment == (
        "Please use a neutral color."
    )

    with pytest.raises(ValidationError):
        normalize_booking_comment("x" * (MAX_BOOKING_COMMENT_LENGTH + 1))


@pytest.mark.django_db
def test_required_phone_pauses_and_resumes_booking(
    bot_booking_context,
    bot_booking_start,
):
    context = bot_booking_context
    selected_ids = (context["service"].id,)
    signature = booking_selection_signature(
        partner=context["partner"],
        selected_service_ids=selected_ids,
    )
    update_schedule_settings(
        actor=context["owner"],
        partner=context["partner"],
        confirmation_mode=PartnerScheduleSettings.ConfirmationMode.AUTO,
        require_phone_for_booking=True,
    )
    _open_window(context, bot_booking_start)
    final = build_booking_final_confirm_reply(
        partner=context["partner"],
        selected_service_ids=selected_ids,
        start_at=bot_booking_start,
        language="en",
        client_comment="First visit",
        now=bot_booking_start - timedelta(hours=1),
    )

    with pytest.raises(ValidationError, match="Phone is required"):
        confirm_booking_reply(
            partner=context["partner"],
            client_profile=context["client"],
            selected_service_ids=selected_ids,
            start_at=bot_booking_start,
            language="en",
            now=bot_booking_start - timedelta(hours=1),
        )

    prompt = _booking_reply_from_callback_data(
        partner=context["partner"],
        client_profile=context["client"],
        data=final.reply_markup.inline_keyboard[0][0].callback_data,
        selected_service_ids=selected_ids,
        summary_signature=signature,
        client_comment="First visit",
    )

    assert prompt.input_mode == BOOKING_INPUT_PHONE
    assert "phone number" in prompt.reply.text
    assert Appointment.objects.filter(partner=context["partner"]).exists() is False

    pending_state = BookingStateValues(
        selected_service_ids=selected_ids,
        summary_signature=signature,
        client_comment="First visit",
        pending_start_at=prompt.pending_start_at,
        date_source=prompt.date_source,
    )
    with pytest.raises(ValidationError):
        _booking_reply_from_phone_input(
            partner=context["partner"],
            client_profile=context["client"],
            booking_state=pending_state,
            phone="not a phone",
        )

    created = _booking_reply_from_phone_input(
        partner=context["partner"],
        client_profile=context["client"],
        booking_state=pending_state,
        phone="+38 (050) 123-45-67",
    )

    context["client"].refresh_from_db()
    appointment = Appointment.objects.get(partner=context["partner"])
    assert created.complete is True
    assert context["client"].phone == "+380501234567"
    assert appointment.client_comment == "First visit"
