from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.appointments.models import Appointment
from apps.appointments.services import (
    AppointmentServiceItemInput,
    create_confirmed_appointment,
    transition_appointment_status,
)
from apps.bot.profile import (
    build_appointment_details_reply,
    build_appointment_history_reply,
    build_cancel_appointment_reply,
    build_future_appointments_reply,
    build_profile_language_reply,
    build_profile_main_reply,
    confirm_cancel_appointment_reply,
    set_profile_language_reply,
)
from apps.catalog.services import create_service, create_service_category
from apps.clients.services import (
    create_manual_client_profile,
    resolve_client_profile_for_telegram_identity,
    upsert_telegram_identity,
)
from apps.finance.models import Penalty
from apps.finance.services import update_penalty_settings
from apps.partners.services import create_partner_with_owner
from apps.schedule.selectors import default_booking_resource_for_partner


@pytest.fixture
def bot_profile_context():
    superuser = User.objects.create_superuser(username="bot-profile-root", password="secret")
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Bot Profile Partner",
        owner_username="bot-profile-owner",
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
    identity = upsert_telegram_identity(
        telegram_user_id=888001,
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
        "service": service,
        "client": client,
    }


@pytest.fixture
def profile_start():
    return (timezone.now() + timedelta(days=4)).replace(
        hour=10,
        minute=0,
        second=0,
        microsecond=0,
    )


def _create_confirmed(context, start_at, *, client=None):
    return create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=client or context["client"],
        start_at=start_at,
        service_items=[AppointmentServiceItemInput(service=context["service"])],
    )


def _button_callbacks(reply):
    return [
        button.callback_data
        for row in reply.reply_markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


@pytest.mark.django_db
def test_profile_main_reply_shows_client_summary_and_actions(
    bot_profile_context,
    profile_start,
):
    context = bot_profile_context
    appointment = _create_confirmed(context, profile_start - timedelta(days=6))
    transition_appointment_status(
        actor=context["owner"],
        appointment=appointment,
        new_status=Appointment.Status.COMPLETED,
    )

    reply = build_profile_main_reply(
        partner=context["partner"],
        client_profile=context["client"],
        now=profile_start - timedelta(hours=1),
    )

    callbacks = _button_callbacks(reply)
    assert "Profile: Ira" in reply.text
    assert "Visits: 1" in reply.text
    assert "pf:future:1" in callbacks
    assert "pf:history:1" in callbacks
    assert "pf:lang" in callbacks
    assert "bk:cat:1" in callbacks


@pytest.mark.django_db
def test_profile_language_reply_updates_client_language(bot_profile_context):
    context = bot_profile_context

    language_reply = build_profile_language_reply(
        partner=context["partner"],
        client_profile=context["client"],
    )
    updated_reply = set_profile_language_reply(
        partner=context["partner"],
        client_profile=context["client"],
        language="ru",
    )

    context["client"].refresh_from_db()
    assert "Choose a language" in language_reply.text
    assert "pf:langset:ru" in _button_callbacks(language_reply)
    assert context["client"].language == "ru"
    assert "Язык изменён" in updated_reply.text


@pytest.mark.django_db
def test_profile_future_history_and_details_are_paginated(
    bot_profile_context,
    profile_start,
):
    context = bot_profile_context
    future = _create_confirmed(context, profile_start)
    completed = _create_confirmed(context, profile_start - timedelta(days=6))
    transition_appointment_status(
        actor=context["owner"],
        appointment=completed,
        new_status=Appointment.Status.COMPLETED,
    )

    future_reply = build_future_appointments_reply(
        partner=context["partner"],
        client_profile=context["client"],
        now=profile_start - timedelta(hours=1),
    )
    history_reply = build_appointment_history_reply(
        partner=context["partner"],
        client_profile=context["client"],
    )
    details_reply = build_appointment_details_reply(
        partner=context["partner"],
        client_profile=context["client"],
        appointment_id=future.id,
        return_action="future",
        return_page=1,
    )

    assert "Future bookings" in future_reply.text
    assert f"pf:details:{future.id.hex}:future:1" in _button_callbacks(future_reply)
    assert "Booking history" in history_reply.text
    assert f"pf:details:{completed.id.hex}:history:1" in _button_callbacks(history_reply)
    assert "Manicure" in details_reply.text
    assert "confirmed" in details_reply.text
    assert f"pf:cancel:{future.id.hex}:future:1" in _button_callbacks(details_reply)


@pytest.mark.django_db
def test_profile_late_cancellation_warns_then_creates_one_penalty(
    bot_profile_context,
    profile_start,
):
    context = bot_profile_context
    update_penalty_settings(
        actor=context["owner"],
        partner=context["partner"],
        penalties_enabled=True,
        late_cancel_penalty_amount=5000,
    )
    appointment = _create_confirmed(context, profile_start)
    cancellation_time = profile_start - timedelta(hours=2)

    warning = build_cancel_appointment_reply(
        partner=context["partner"],
        client_profile=context["client"],
        appointment_id=appointment.id,
        now=cancellation_time,
    )
    result = confirm_cancel_appointment_reply(
        partner=context["partner"],
        client_profile=context["client"],
        appointment_id=appointment.id,
        accept_late_penalty=True,
        now=cancellation_time,
    )

    callbacks = _button_callbacks(warning)
    assert "Late cancellation" in warning.text
    assert f"pf:cancelgo:{appointment.id.hex}:future:1:1" in callbacks
    assert "Penalty: 50.00 UAH" in result.text
    appointment.refresh_from_db()
    assert appointment.status == Appointment.Status.CANCELLED_BY_CLIENT
    assert (
        Penalty.objects.filter(
            source_appointment=appointment,
            reason=Penalty.Reason.LATE_CANCEL,
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_profile_late_cancellation_without_penalties_is_blocked(
    bot_profile_context,
    profile_start,
):
    context = bot_profile_context
    appointment = _create_confirmed(context, profile_start)

    reply = build_cancel_appointment_reply(
        partner=context["partner"],
        client_profile=context["client"],
        appointment_id=appointment.id,
        now=profile_start - timedelta(hours=2),
    )

    assert "Cancellation is no longer available" in reply.text
    assert all("cancelgo" not in callback for callback in _button_callbacks(reply))
    appointment.refresh_from_db()
    assert appointment.status == Appointment.Status.CONFIRMED


@pytest.mark.django_db
def test_profile_final_appointment_details_do_not_offer_cancellation(
    bot_profile_context,
    profile_start,
):
    context = bot_profile_context
    appointment = _create_confirmed(context, profile_start - timedelta(days=6))
    transition_appointment_status(
        actor=context["owner"],
        appointment=appointment,
        new_status=Appointment.Status.COMPLETED,
    )

    reply = build_appointment_details_reply(
        partner=context["partner"],
        client_profile=context["client"],
        appointment_id=appointment.id,
        return_action="history",
        now=profile_start,
    )

    assert all("cancel" not in callback for callback in _button_callbacks(reply))


@pytest.mark.django_db
def test_profile_details_rejects_other_client_appointment(
    bot_profile_context,
    profile_start,
):
    context = bot_profile_context
    other_client = create_manual_client_profile(
        actor=context["owner"],
        partner=context["partner"],
        name="Other Client",
    )
    other_appointment = _create_confirmed(
        context,
        profile_start + timedelta(days=1),
        client=other_client,
    )

    with pytest.raises(ValidationError):
        build_appointment_details_reply(
            partner=context["partner"],
            client_profile=context["client"],
            appointment_id=other_appointment.id,
        )
