from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.appointments.services import (
    AppointmentServiceItemInput,
    create_confirmed_appointment,
)
from apps.bot.broadcasts import build_broadcast_action_reply
from apps.catalog.services import create_service, create_service_category
from apps.clients.services import (
    resolve_client_profile_for_telegram_identity,
    upsert_telegram_identity,
)
from apps.partners.services import create_partner_with_owner
from apps.schedule.selectors import default_booking_resource_for_partner


@pytest.fixture
def broadcast_action_context():
    superuser = User.objects.create_superuser(
        username="broadcast-action-root",
        password="secret",
    )
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Broadcast Action Partner",
        owner_username="broadcast-action-owner",
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
        price_amount=50_000,
        duration_minutes=60,
    )
    identity = upsert_telegram_identity(
        telegram_user_id=994001,
        username="broadcast-client",
        first_name="Ira",
        language_code="en",
    )
    client = resolve_client_profile_for_telegram_identity(
        partner=created.partner,
        telegram_identity=identity,
    )
    appointment = create_confirmed_appointment(
        actor=created.owner,
        partner=created.partner,
        booking_resource=default_booking_resource_for_partner(created.partner),
        client_profile=client,
        start_at=timezone.now() + timedelta(days=2),
        service_items=[AppointmentServiceItemInput(service=service)],
    )
    return {
        "superuser": superuser,
        "partner": created.partner,
        "owner": created.owner,
        "category": category,
        "client": client,
        "appointment": appointment,
    }


def _callbacks(reply):
    return [
        button.callback_data
        for row in reply.reply_markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


@pytest.mark.django_db
def test_broadcast_book_action_opens_clean_booking_entry(broadcast_action_context):
    context = broadcast_action_context

    reply = build_broadcast_action_reply(
        partner=context["partner"],
        client_profile=context["client"],
        callback_data="broadcast:book",
    )

    assert reply.starts_booking_flow is True
    assert "Choose a service category" in reply.text
    assert _callbacks(reply)[0] == f"bk:svc:{context['category'].id.hex}:1"


@pytest.mark.django_db
def test_broadcast_my_appointments_opens_client_future_list(broadcast_action_context):
    context = broadcast_action_context

    reply = build_broadcast_action_reply(
        partner=context["partner"],
        client_profile=context["client"],
        callback_data="broadcast:my_appointments",
    )

    assert reply.starts_booking_flow is False
    assert "Manicure" in reply.text
    assert f"pf:details:{context['appointment'].id.hex}:future:1" in _callbacks(reply)


@pytest.mark.django_db
def test_broadcast_action_rejects_cross_partner_client(broadcast_action_context):
    context = broadcast_action_context
    other = create_partner_with_owner(
        actor=context["superuser"],
        partner_name="Other Broadcast Action Partner",
        owner_username="other-broadcast-action-owner",
        owner_password="secret",
    )

    with pytest.raises(ValidationError):
        build_broadcast_action_reply(
            partner=other.partner,
            client_profile=context["client"],
            callback_data="broadcast:book",
        )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "callback_data",
    ["broadcast", "broadcast:unknown", "broadcast:book:extra", "other:book"],
)
def test_broadcast_action_rejects_unsupported_callback(
    broadcast_action_context,
    callback_data,
):
    context = broadcast_action_context

    with pytest.raises(ValidationError):
        build_broadcast_action_reply(
            partner=context["partner"],
            client_profile=context["client"],
            callback_data=callback_data,
        )
