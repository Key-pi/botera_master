from datetime import timedelta
from uuid import UUID

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.appointments.models import Appointment
from apps.appointments.services import (
    AppointmentServiceItemInput,
    create_requested_appointment,
)
from apps.bot.master import (
    build_master_booking_request_reply,
    master_booking_request_markup,
)
from apps.bot.models import PartnerTelegramContact
from apps.bot.routers import _master_reply_from_callback_data
from apps.bot.selectors import master_telegram_contact_for_user
from apps.catalog.services import create_service, create_service_category
from apps.clients.services import (
    resolve_client_profile_for_telegram_identity,
    upsert_telegram_identity,
)
from apps.notifications.models import ScheduledNotification
from apps.partners.services import create_partner_with_owner
from apps.schedule.selectors import default_booking_resource_for_partner


@pytest.fixture
def master_request_context():
    superuser = User.objects.create_superuser(username="master-request-root", password="secret")
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Master Request Partner",
        owner_username="master-request-owner",
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
        telegram_user_id=991001,
        username="booking-client",
        first_name="Ira",
    )
    client = resolve_client_profile_for_telegram_identity(
        partner=created.partner,
        telegram_identity=identity,
    )
    return {
        "superuser": superuser,
        "partner": created.partner,
        "owner": created.owner,
        "resource": default_booking_resource_for_partner(created.partner),
        "service": service,
        "client": client,
    }


def _create_request(context, *, start_at=None):
    return create_requested_appointment(
        actor=None,
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=start_at or timezone.now() + timedelta(days=3),
        service_items=[AppointmentServiceItemInput(service=context["service"])],
        client_comment="Please call before the visit.",
    )


def _callbacks(reply):
    return [
        button.callback_data
        for row in reply.reply_markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


@pytest.mark.django_db
def test_master_request_reply_contains_compact_confirm_and_decline_actions(
    master_request_context,
):
    context = master_request_context
    appointment = _create_request(context)

    reply = build_master_booking_request_reply(
        partner=context["partner"],
        appointment_id=appointment.id,
    )

    callbacks = _callbacks(reply)
    assert "Нова заявка" in reply.text
    assert "Please call before the visit." in reply.text
    assert callbacks == [
        f"mr:confirm:{appointment.id.hex}",
        f"mr:decline:{appointment.id.hex}",
    ]
    assert all(len(callback.encode("utf-8")) <= 64 for callback in callbacks)


@pytest.mark.django_db
def test_master_confirm_callback_uses_owner_service_and_notifies_client(
    master_request_context,
    django_capture_on_commit_callbacks,
):
    context = master_request_context
    with django_capture_on_commit_callbacks(execute=True):
        appointment = _create_request(context)

    with django_capture_on_commit_callbacks(execute=True):
        reply = _master_reply_from_callback_data(
            partner=context["partner"],
            actor=context["owner"],
            data=f"mr:confirm:{appointment.id.hex}",
        )

    appointment.refresh_from_db()
    assert appointment.status == Appointment.Status.CONFIRMED
    assert "Заявку підтверджено" in reply.text
    assert ScheduledNotification.objects.filter(
        appointment=appointment,
        type=ScheduledNotification.Type.CLIENT_BOOKING_REQUEST_CONFIRMED,
    ).exists()


@pytest.mark.django_db
def test_master_decline_requires_confirmation_then_notifies_client(
    master_request_context,
    django_capture_on_commit_callbacks,
):
    context = master_request_context
    with django_capture_on_commit_callbacks(execute=True):
        appointment = _create_request(context)

    prompt = _master_reply_from_callback_data(
        partner=context["partner"],
        actor=context["owner"],
        data=f"mr:decline:{appointment.id.hex}",
    )
    appointment.refresh_from_db()
    assert appointment.status == Appointment.Status.REQUESTED
    assert _callbacks(prompt)[0] == f"mr:declinego:{appointment.id.hex}"

    with django_capture_on_commit_callbacks(execute=True):
        reply = _master_reply_from_callback_data(
            partner=context["partner"],
            actor=context["owner"],
            data=f"mr:declinego:{appointment.id.hex}",
        )

    appointment.refresh_from_db()
    assert appointment.status == Appointment.Status.CANCELLED_BY_MASTER
    assert "Заявку відхилено" in reply.text
    assert ScheduledNotification.objects.filter(
        appointment=appointment,
        type=ScheduledNotification.Type.CLIENT_BOOKING_REQUEST_DECLINED,
    ).exists()


@pytest.mark.django_db
def test_second_competing_request_cannot_be_confirmed(master_request_context):
    context = master_request_context
    start_at = timezone.now() + timedelta(days=3)
    first = _create_request(context, start_at=start_at)
    second = _create_request(context, start_at=start_at)

    _master_reply_from_callback_data(
        partner=context["partner"],
        actor=context["owner"],
        data=f"mr:confirm:{first.id.hex}",
    )
    with pytest.raises(ValidationError):
        _master_reply_from_callback_data(
            partner=context["partner"],
            actor=context["owner"],
            data=f"mr:confirm:{second.id.hex}",
        )

    second.refresh_from_db()
    assert second.status == Appointment.Status.REQUESTED


@pytest.mark.django_db
def test_master_callback_cannot_reach_another_partner_request(master_request_context):
    context = master_request_context
    appointment = _create_request(context)
    other = create_partner_with_owner(
        actor=context["superuser"],
        partner_name="Other Master",
        owner_username="other-master-owner",
        owner_password="secret",
    )

    with pytest.raises(ValidationError):
        _master_reply_from_callback_data(
            partner=other.partner,
            actor=other.owner,
            data=f"mr:confirm:{appointment.id.hex}",
        )

    appointment.refresh_from_db()
    assert appointment.status == Appointment.Status.REQUESTED


@pytest.mark.django_db
def test_master_contact_resolution_is_partner_scoped(master_request_context):
    context = master_request_context
    identity = upsert_telegram_identity(
        telegram_user_id=991002,
        username="master-contact",
    )
    contact = PartnerTelegramContact.objects.create(
        partner=context["partner"],
        telegram_identity=identity,
        user=context["owner"],
        is_primary=True,
    )
    other = create_partner_with_owner(
        actor=context["superuser"],
        partner_name="Contact Other Master",
        owner_username="contact-other-owner",
        owner_password="secret",
    )

    assert (
        master_telegram_contact_for_user(
            partner=context["partner"],
            telegram_user_id=identity.telegram_user_id,
        )
        == contact
    )
    assert (
        master_telegram_contact_for_user(
            partner=other.partner,
            telegram_user_id=identity.telegram_user_id,
        )
        is None
    )


@pytest.mark.django_db
def test_master_request_markup_accepts_uuid_without_database_lookup():
    appointment_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    markup = master_booking_request_markup(
        appointment_id=appointment_id,
        language="en",
    )

    callbacks = [button.callback_data for button in markup.inline_keyboard[0]]
    assert callbacks == [
        "mr:confirm:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "mr:decline:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    ]
