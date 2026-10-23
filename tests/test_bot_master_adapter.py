from datetime import timedelta
from uuid import UUID

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.appointments.models import Appointment, AppointmentProposal
from apps.appointments.services import (
    AppointmentServiceItemInput,
    create_confirmed_appointment,
    create_requested_appointment,
)
from apps.bot.master import (
    MASTER_PROPOSAL_SELECTION_LIMIT,
    build_master_booking_request_reply,
    master_booking_request_markup,
)
from apps.bot.models import PartnerTelegramContact
from apps.bot.routers import (
    MasterProposalStateValues,
    _master_callback_result_from_data,
    _master_reply_from_callback_data,
)
from apps.bot.selectors import master_telegram_contact_for_user
from apps.catalog.services import create_service, create_service_category
from apps.clients.services import (
    resolve_client_profile_for_telegram_identity,
    upsert_telegram_identity,
)
from apps.notifications.models import ScheduledNotification
from apps.partners.services import create_partner_with_owner
from apps.schedule.selectors import default_booking_resource_for_partner
from apps.schedule.services import create_availability_window, update_schedule_settings
from core.timezones import to_partner_time


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
def test_master_request_reply_contains_compact_request_actions(
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
        f"mr:propose:{appointment.id.hex}",
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

    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert callbacks == [
        "mr:confirm:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "mr:decline:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "mr:propose:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    ]


@pytest.mark.django_db
def test_master_builds_and_sends_multiple_requested_alternatives(
    master_request_context,
    django_capture_on_commit_callbacks,
):
    context = master_request_context
    now = timezone.now().replace(second=0, microsecond=0)
    start_at = (now + timedelta(days=3)).replace(hour=10, minute=0)
    update_schedule_settings(
        actor=context["owner"],
        partner=context["partner"],
        confirmation_mode="manual",
        slot_step_minutes=30,
    )
    create_availability_window(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        start_at=start_at,
        end_at=start_at + timedelta(hours=4),
    )
    with django_capture_on_commit_callbacks(execute=True):
        appointment = _create_request(context, start_at=start_at)
    stale_request = ScheduledNotification.objects.get(
        appointment=appointment,
        type=ScheduledNotification.Type.STALE_REQUESTED,
    )

    first_start = start_at + timedelta(minutes=30)
    second_start = start_at + timedelta(hours=1)
    first = _master_callback_result_from_data(
        partner=context["partner"],
        actor=context["owner"],
        data=f"mr:ptoggle:{appointment.id.hex}:{int(first_start.timestamp())}:1",
        proposal_state=MasterProposalStateValues(appointment_id=appointment.id),
    )
    second = _master_callback_result_from_data(
        partner=context["partner"],
        actor=context["owner"],
        data=f"mr:ptoggle:{appointment.id.hex}:{int(second_start.timestamp())}:1",
        proposal_state=MasterProposalStateValues(
            appointment_id=appointment.id,
            selected_start_ats=first.selected_start_ats,
        ),
    )
    review = _master_callback_result_from_data(
        partner=context["partner"],
        actor=context["owner"],
        data=f"mr:preview:{appointment.id.hex}",
        proposal_state=MasterProposalStateValues(
            appointment_id=appointment.id,
            selected_start_ats=second.selected_start_ats,
        ),
    )
    with django_capture_on_commit_callbacks(execute=True):
        sent = _master_callback_result_from_data(
            partner=context["partner"],
            actor=context["owner"],
            data=f"mr:psend:{appointment.id.hex}",
            proposal_state=MasterProposalStateValues(
                appointment_id=appointment.id,
                selected_start_ats=second.selected_start_ats,
            ),
        )

    proposal = AppointmentProposal.objects.get(appointment=appointment)
    stale_request.refresh_from_db()
    assert second.selected_start_ats == (first_start, second_start)
    assert (
        to_partner_time(first_start, context["partner"].timezone).strftime("%Y-%m-%d %H:%M")
        in review.reply.text
    )
    assert (
        to_partner_time(second_start, context["partner"].timezone).strftime("%Y-%m-%d %H:%M")
        in review.reply.text
    )
    assert proposal.type == AppointmentProposal.Type.REQUESTED_ALTERNATIVES
    assert proposal.status == AppointmentProposal.Status.PENDING
    assert stale_request.status == ScheduledNotification.Status.STALE
    assert list(proposal.options.values_list("start_at", flat=True)) == [
        first_start,
        second_start,
    ]
    assert "надіслано варіанти часу: 2" in sent.reply.text
    assert ScheduledNotification.objects.filter(
        appointment=appointment,
        appointment_proposal=proposal,
        type=ScheduledNotification.Type.CLIENT_APPOINTMENT_PROPOSAL,
    ).exists()


@pytest.mark.django_db
def test_master_alternative_send_rechecks_slot_availability(master_request_context):
    context = master_request_context
    now = timezone.now().replace(second=0, microsecond=0)
    start_at = (now + timedelta(days=3)).replace(hour=10, minute=0)
    alternative_start = start_at + timedelta(hours=1)
    update_schedule_settings(
        actor=context["owner"],
        partner=context["partner"],
        confirmation_mode="manual",
        slot_step_minutes=30,
    )
    create_availability_window(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        start_at=start_at,
        end_at=start_at + timedelta(hours=4),
    )
    appointment = _create_request(context, start_at=start_at)
    create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=alternative_start,
        service_items=[AppointmentServiceItemInput(service=context["service"])],
    )

    with pytest.raises(ValidationError):
        _master_callback_result_from_data(
            partner=context["partner"],
            actor=context["owner"],
            data=f"mr:psend:{appointment.id.hex}",
            proposal_state=MasterProposalStateValues(
                appointment_id=appointment.id,
                selected_start_ats=(alternative_start,),
            ),
        )

    assert not AppointmentProposal.objects.filter(appointment=appointment).exists()


@pytest.mark.django_db
def test_master_proposal_selection_limit_keeps_existing_options(master_request_context):
    context = master_request_context
    now = timezone.now().replace(second=0, microsecond=0)
    start_at = (now + timedelta(days=3)).replace(hour=9, minute=0)
    update_schedule_settings(
        actor=context["owner"],
        partner=context["partner"],
        confirmation_mode="manual",
        slot_step_minutes=30,
    )
    create_availability_window(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        start_at=start_at,
        end_at=start_at + timedelta(hours=6),
    )
    appointment = _create_request(context, start_at=start_at)
    selected = tuple(
        start_at + timedelta(minutes=30 * offset)
        for offset in range(1, MASTER_PROPOSAL_SELECTION_LIMIT + 1)
    )
    extra_start = start_at + timedelta(hours=3)

    result = _master_callback_result_from_data(
        partner=context["partner"],
        actor=context["owner"],
        data=f"mr:ptoggle:{appointment.id.hex}:{int(extra_start.timestamp())}:1",
        proposal_state=MasterProposalStateValues(
            appointment_id=appointment.id,
            selected_start_ats=selected,
        ),
    )

    assert result.selected_start_ats == selected
    assert "щонайбільше 5 варіантів" in result.reply.text
    assert all(len(callback.encode("utf-8")) <= 64 for callback in _callbacks(result.reply))
