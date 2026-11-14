from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.appointments.models import Appointment, AppointmentProposal
from apps.appointments.services import (
    AppointmentServiceItemInput,
    create_confirmed_appointment,
)
from apps.bot.master_workspace import PERIOD_UPCOMING, build_master_appointment_detail_reply
from apps.bot.routers import MasterProposalStateValues, _master_reschedule_result_from_data
from apps.catalog.services import create_service, create_service_category
from apps.clients.services import (
    create_manual_client_profile,
    resolve_client_profile_for_telegram_identity,
    upsert_telegram_identity,
)
from apps.notifications.models import ScheduledNotification
from apps.partners.services import create_partner_with_owner
from apps.schedule.selectors import default_booking_resource_for_partner
from apps.schedule.services import create_availability_window, update_schedule_settings
from core.timezones import to_partner_time


@pytest.fixture
def master_reschedule_context():
    superuser = User.objects.create_superuser(
        username="master-reschedule-root",
        password="secret",
    )
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Master Reschedule",
        owner_username="master-reschedule-owner",
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
        telegram_user_id=993001,
        username="reschedule-client",
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


def _callbacks(reply):
    return [
        button.callback_data
        for row in reply.reply_markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


def _prepare_future_appointment(context):
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
        end_at=start_at + timedelta(hours=5),
    )
    appointment = create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=start_at,
        service_items=[AppointmentServiceItemInput(service=context["service"])],
    )
    return appointment, start_at


@pytest.mark.django_db
def test_future_confirmed_details_offer_master_reschedule(master_reschedule_context):
    context = master_reschedule_context
    appointment, _start_at = _prepare_future_appointment(context)

    reply = build_master_appointment_detail_reply(
        partner=context["partner"],
        actor=context["owner"],
        appointment_id=appointment.id,
        return_period=PERIOD_UPCOMING,
    )

    assert f"rs:d:{appointment.id.hex}:1" in _callbacks(reply)
    assert all(len(callback.encode("utf-8")) <= 64 for callback in _callbacks(reply))


@pytest.mark.django_db
def test_manual_client_without_telegram_does_not_get_reschedule_proposal_action(
    master_reschedule_context,
):
    context = master_reschedule_context
    manual_client = create_manual_client_profile(
        actor=context["owner"],
        partner=context["partner"],
        name="Manual Client",
    )
    appointment = create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=manual_client,
        start_at=timezone.now() + timedelta(days=2),
        service_items=[AppointmentServiceItemInput(service=context["service"])],
    )

    reply = build_master_appointment_detail_reply(
        partner=context["partner"],
        actor=context["owner"],
        appointment_id=appointment.id,
        return_period=PERIOD_UPCOMING,
    )

    assert not any(callback.startswith("rs:") for callback in _callbacks(reply))


@pytest.mark.django_db
def test_master_sends_multiple_reschedule_options_without_moving_appointment(
    master_reschedule_context,
    django_capture_on_commit_callbacks,
):
    context = master_reschedule_context
    appointment, original_start = _prepare_future_appointment(context)
    first_start = original_start + timedelta(hours=1)
    second_start = original_start + timedelta(hours=2)
    local_date = to_partner_time(first_start, context["partner"].timezone).date()

    first = _master_reschedule_result_from_data(
        partner=context["partner"],
        actor=context["owner"],
        data=(f"rs:t:{appointment.id.hex}:{int(first_start.timestamp())}:{local_date:%Y%m%d}:1"),
        proposal_state=MasterProposalStateValues(appointment_id=appointment.id),
    )
    second = _master_reschedule_result_from_data(
        partner=context["partner"],
        actor=context["owner"],
        data=(f"rs:t:{appointment.id.hex}:{int(second_start.timestamp())}:{local_date:%Y%m%d}:1"),
        proposal_state=MasterProposalStateValues(
            appointment_id=appointment.id,
            selected_start_ats=first.selected_start_ats,
        ),
    )
    assert all(len(callback.encode("utf-8")) <= 64 for callback in _callbacks(second.reply))
    review = _master_reschedule_result_from_data(
        partner=context["partner"],
        actor=context["owner"],
        data=f"rs:v:{appointment.id.hex}",
        proposal_state=MasterProposalStateValues(
            appointment_id=appointment.id,
            selected_start_ats=second.selected_start_ats,
        ),
    )
    with django_capture_on_commit_callbacks(execute=True):
        sent = _master_reschedule_result_from_data(
            partner=context["partner"],
            actor=context["owner"],
            data=f"rs:g:{appointment.id.hex}",
            proposal_state=MasterProposalStateValues(
                appointment_id=appointment.id,
                selected_start_ats=second.selected_start_ats,
            ),
        )

    appointment.refresh_from_db()
    proposal = AppointmentProposal.objects.get(appointment=appointment)
    assert second.selected_start_ats == (first_start, second_start)
    assert proposal.type == AppointmentProposal.Type.RESCHEDULE_CONFIRMED
    assert proposal.status == AppointmentProposal.Status.PENDING
    assert list(proposal.options.values_list("start_at", flat=True)) == [
        first_start,
        second_start,
    ]
    assert appointment.status == Appointment.Status.CONFIRMED
    assert appointment.start_at == original_start
    assert "ці варіанти перенесення" in review.reply.text
    assert "надіслано варіанти перенесення: 2" in sent.reply.text
    assert ScheduledNotification.objects.filter(
        appointment=appointment,
        appointment_proposal=proposal,
        type=ScheduledNotification.Type.CLIENT_APPOINTMENT_PROPOSAL,
    ).exists()


@pytest.mark.django_db
def test_master_reschedule_send_rechecks_slot_availability(master_reschedule_context):
    context = master_reschedule_context
    appointment, original_start = _prepare_future_appointment(context)
    alternative_start = original_start + timedelta(hours=1)
    create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=alternative_start,
        service_items=[AppointmentServiceItemInput(service=context["service"])],
    )

    with pytest.raises(ValidationError):
        _master_reschedule_result_from_data(
            partner=context["partner"],
            actor=context["owner"],
            data=f"rs:g:{appointment.id.hex}",
            proposal_state=MasterProposalStateValues(
                appointment_id=appointment.id,
                selected_start_ats=(alternative_start,),
            ),
        )

    assert not AppointmentProposal.objects.filter(appointment=appointment).exists()


@pytest.mark.django_db
def test_master_reschedule_cannot_reach_foreign_partner_appointment(
    master_reschedule_context,
):
    context = master_reschedule_context
    appointment, _start_at = _prepare_future_appointment(context)
    other = create_partner_with_owner(
        actor=context["superuser"],
        partner_name="Other Reschedule",
        owner_username="other-reschedule-owner",
        owner_password="secret",
    )

    with pytest.raises(ValidationError):
        _master_reschedule_result_from_data(
            partner=other.partner,
            actor=other.owner,
            data=f"rs:d:{appointment.id.hex}:1",
        )
