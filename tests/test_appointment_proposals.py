from datetime import timedelta

import pytest
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.test import RequestFactory
from django.utils import timezone

from apps.accounts.models import User
from apps.appointments.admin import AppointmentProposalOptionAdmin
from apps.appointments.models import Appointment, AppointmentProposal, AppointmentProposalOption
from apps.appointments.proposals import (
    AppointmentProposalOptionInput,
    accept_appointment_proposal_option,
    create_appointment_proposal,
    decline_appointment_proposal,
    expire_pending_appointment_proposals,
)
from apps.appointments.services import (
    AppointmentServiceItemInput,
    create_confirmed_appointment,
    create_requested_appointment,
)
from apps.appointments.tasks import expire_pending_appointment_proposals_task
from apps.catalog.services import create_service, create_service_category
from apps.clients.services import create_manual_client_profile
from apps.notifications.models import ScheduledNotification
from apps.partners.services import create_partner_with_owner
from apps.schedule.selectors import default_booking_resource_for_partner


@pytest.fixture
def proposal_context():
    superuser = User.objects.create_superuser(username="root", password="secret")
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Proposal Partner",
        owner_username="proposal-owner",
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
    second_service = create_service(
        actor=created.owner,
        partner=created.partner,
        category=category,
        name="Pedicure",
        price_amount=80000,
        duration_minutes=90,
    )
    client = create_manual_client_profile(
        actor=created.owner,
        partner=created.partner,
        name="Ira",
        phone="+380501111111",
    )
    other_client = create_manual_client_profile(
        actor=created.owner,
        partner=created.partner,
        name="Olia",
        phone="+380502222222",
    )
    return {
        "partner": created.partner,
        "owner": created.owner,
        "resource": default_booking_resource_for_partner(created.partner),
        "service": service,
        "second_service": second_service,
        "client": client,
        "other_client": other_client,
    }


@pytest.fixture
def rounded_future_start():
    return (timezone.now() + timedelta(days=4)).replace(minute=0, second=0, microsecond=0)


def _service_input(service):
    return AppointmentServiceItemInput(service=service)


def _option_start(start_at, days=1):
    return start_at + timedelta(days=days)


@pytest.mark.django_db
def test_new_pending_proposal_supersedes_previous_pending(
    proposal_context,
    rounded_future_start,
):
    context = proposal_context
    appointment = create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=rounded_future_start,
        service_items=[_service_input(context["service"])],
    )
    first = create_appointment_proposal(
        actor=context["owner"],
        appointment=appointment,
        proposal_type=AppointmentProposal.Type.RESCHEDULE_CONFIRMED,
        options=[AppointmentProposalOptionInput(start_at=_option_start(rounded_future_start))],
    )
    second = create_appointment_proposal(
        actor=context["owner"],
        appointment=appointment,
        proposal_type=AppointmentProposal.Type.RESCHEDULE_CONFIRMED,
        options=[
            AppointmentProposalOptionInput(start_at=_option_start(rounded_future_start, days=2))
        ],
    )

    first.refresh_from_db()

    assert first.status == AppointmentProposal.Status.SUPERSEDED
    assert second.status == AppointmentProposal.Status.PENDING


@pytest.mark.django_db
def test_confirmed_appointment_remains_unchanged_while_reschedule_proposal_is_pending(
    proposal_context,
    rounded_future_start,
):
    context = proposal_context
    appointment = create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=rounded_future_start,
        service_items=[_service_input(context["service"])],
    )

    create_appointment_proposal(
        actor=context["owner"],
        appointment=appointment,
        proposal_type=AppointmentProposal.Type.RESCHEDULE_CONFIRMED,
        options=[AppointmentProposalOptionInput(start_at=_option_start(rounded_future_start))],
    )
    appointment.refresh_from_db()

    assert appointment.status == Appointment.Status.CONFIRMED
    assert appointment.start_at == rounded_future_start


@pytest.mark.django_db
def test_accepting_requested_alternative_confirms_appointment_after_recheck(
    proposal_context,
    rounded_future_start,
):
    context = proposal_context
    requested = create_requested_appointment(
        actor=None,
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=rounded_future_start,
        service_items=[_service_input(context["service"])],
    )
    proposal = create_appointment_proposal(
        actor=context["owner"],
        appointment=requested,
        proposal_type=AppointmentProposal.Type.REQUESTED_ALTERNATIVES,
        options=[AppointmentProposalOptionInput(start_at=_option_start(rounded_future_start))],
    )
    option = proposal.options.get()

    accepted = accept_appointment_proposal_option(
        client_profile=context["client"],
        option=option,
    )
    proposal.refresh_from_db()

    assert accepted.status == Appointment.Status.CONFIRMED
    assert accepted.source == Appointment.Source.PROPOSAL
    assert accepted.start_at == _option_start(rounded_future_start)
    assert proposal.status == AppointmentProposal.Status.ACCEPTED
    assert proposal.selected_option_id == option.id


@pytest.mark.django_db
def test_requested_alternatives_pause_and_resume_stale_master_reminders(
    proposal_context,
    rounded_future_start,
    django_capture_on_commit_callbacks,
):
    context = proposal_context
    with django_capture_on_commit_callbacks(execute=True):
        requested = create_requested_appointment(
            actor=None,
            partner=context["partner"],
            booking_resource=context["resource"],
            client_profile=context["client"],
            start_at=rounded_future_start,
            service_items=[_service_input(context["service"])],
        )
    initial_stale = ScheduledNotification.objects.get(
        appointment=requested,
        type=ScheduledNotification.Type.STALE_REQUESTED,
        stale_sequence=0,
    )

    with django_capture_on_commit_callbacks(execute=True):
        proposal = create_appointment_proposal(
            actor=context["owner"],
            appointment=requested,
            proposal_type=AppointmentProposal.Type.REQUESTED_ALTERNATIVES,
            options=[AppointmentProposalOptionInput(start_at=_option_start(rounded_future_start))],
        )
    initial_stale.refresh_from_db()

    with django_capture_on_commit_callbacks(execute=True):
        decline_appointment_proposal(
            client_profile=context["client"],
            proposal=proposal,
        )

    resumed = ScheduledNotification.objects.get(
        appointment=requested,
        type=ScheduledNotification.Type.STALE_REQUESTED,
        stale_sequence=1,
    )
    assert initial_stale.status == ScheduledNotification.Status.STALE
    assert resumed.status == ScheduledNotification.Status.QUEUED


@pytest.mark.django_db
def test_accepting_option_rechecks_availability_before_changing_appointment(
    proposal_context,
    rounded_future_start,
):
    context = proposal_context
    requested = create_requested_appointment(
        actor=None,
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=rounded_future_start,
        service_items=[_service_input(context["service"])],
    )
    proposal = create_appointment_proposal(
        actor=context["owner"],
        appointment=requested,
        proposal_type=AppointmentProposal.Type.REQUESTED_ALTERNATIVES,
        options=[AppointmentProposalOptionInput(start_at=_option_start(rounded_future_start))],
    )
    create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["other_client"],
        start_at=_option_start(rounded_future_start),
        service_items=[_service_input(context["service"])],
    )

    with pytest.raises(ValidationError):
        accept_appointment_proposal_option(
            client_profile=context["client"],
            option=proposal.options.get(),
        )

    requested.refresh_from_db()
    proposal.refresh_from_db()
    assert requested.status == Appointment.Status.REQUESTED
    assert proposal.status == AppointmentProposal.Status.PENDING


@pytest.mark.django_db
def test_accepting_confirmed_reschedule_updates_existing_appointment(
    proposal_context,
    rounded_future_start,
):
    context = proposal_context
    appointment = create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=rounded_future_start,
        service_items=[_service_input(context["service"])],
    )
    proposal = create_appointment_proposal(
        actor=context["owner"],
        appointment=appointment,
        proposal_type=AppointmentProposal.Type.RESCHEDULE_CONFIRMED,
        options=[AppointmentProposalOptionInput(start_at=_option_start(rounded_future_start))],
    )

    accepted = accept_appointment_proposal_option(
        client_profile=context["client"],
        option=proposal.options.get(),
    )

    assert accepted.id == appointment.id
    assert accepted.status == Appointment.Status.CONFIRMED
    assert accepted.start_at == _option_start(rounded_future_start)
    assert accepted.end_at == _option_start(rounded_future_start) + timedelta(hours=1)


@pytest.mark.django_db
def test_appointment_change_proposal_replaces_service_snapshots_on_accept(
    proposal_context,
    rounded_future_start,
):
    context = proposal_context
    appointment = create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=rounded_future_start,
        service_items=[_service_input(context["service"])],
    )
    proposal = create_appointment_proposal(
        actor=context["owner"],
        appointment=appointment,
        proposal_type=AppointmentProposal.Type.APPOINTMENT_CHANGE,
        options=[
            AppointmentProposalOptionInput(
                service_items=[_service_input(context["second_service"])],
            )
        ],
    )

    accepted = accept_appointment_proposal_option(
        client_profile=context["client"],
        option=proposal.options.get(),
    )
    item = accepted.service_items.get()

    assert accepted.start_at == rounded_future_start
    assert accepted.end_at == rounded_future_start + timedelta(minutes=90)
    assert accepted.total_amount == 80000
    assert item.service_name_snapshot == "Pedicure"
    assert item.duration_minutes == 90


@pytest.mark.django_db
def test_requested_alternative_expiry_cancels_requested_appointment(
    proposal_context,
    rounded_future_start,
):
    context = proposal_context
    requested = create_requested_appointment(
        actor=None,
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=rounded_future_start,
        service_items=[_service_input(context["service"])],
    )
    create_appointment_proposal(
        actor=context["owner"],
        appointment=requested,
        proposal_type=AppointmentProposal.Type.REQUESTED_ALTERNATIVES,
        options=[AppointmentProposalOptionInput(start_at=_option_start(rounded_future_start))],
        expires_at=timezone.now() - timedelta(minutes=1),
    )

    assert expire_pending_appointment_proposals(now=timezone.now()) == 1
    requested.refresh_from_db()

    assert requested.status == Appointment.Status.CANCELLED_BY_MASTER
    assert requested.cancelled_at is not None


@pytest.mark.django_db
def test_confirmed_reschedule_expiry_leaves_appointment_unchanged(
    proposal_context,
    rounded_future_start,
):
    context = proposal_context
    appointment = create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=rounded_future_start,
        service_items=[_service_input(context["service"])],
    )
    proposal = create_appointment_proposal(
        actor=context["owner"],
        appointment=appointment,
        proposal_type=AppointmentProposal.Type.RESCHEDULE_CONFIRMED,
        options=[AppointmentProposalOptionInput(start_at=_option_start(rounded_future_start))],
        expires_at=timezone.now() - timedelta(minutes=1),
    )

    assert expire_pending_appointment_proposals(now=timezone.now()) == 1
    appointment.refresh_from_db()
    proposal.refresh_from_db()

    assert appointment.status == Appointment.Status.CONFIRMED
    assert appointment.start_at == rounded_future_start
    assert proposal.status == AppointmentProposal.Status.EXPIRED


@pytest.mark.django_db
def test_expiry_task_is_idempotent_and_queues_master_notice(
    proposal_context,
    rounded_future_start,
    django_capture_on_commit_callbacks,
):
    context = proposal_context
    appointment = create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=rounded_future_start,
        service_items=[_service_input(context["service"])],
    )
    proposal = create_appointment_proposal(
        actor=context["owner"],
        appointment=appointment,
        proposal_type=AppointmentProposal.Type.RESCHEDULE_CONFIRMED,
        options=[AppointmentProposalOptionInput(start_at=_option_start(rounded_future_start))],
        expires_at=timezone.now() - timedelta(minutes=1),
    )

    with django_capture_on_commit_callbacks(execute=True):
        first_count = expire_pending_appointment_proposals_task()
    second_count = expire_pending_appointment_proposals_task()

    assert first_count == 1
    assert second_count == 0
    assert ScheduledNotification.objects.filter(
        appointment_proposal=proposal,
        type=ScheduledNotification.Type.MASTER_APPOINTMENT_PROPOSAL_EXPIRED,
        status=ScheduledNotification.Status.QUEUED,
    ).exists()


@pytest.mark.django_db
def test_proposal_option_admin_queryset_is_partner_scoped(
    proposal_context,
    rounded_future_start,
):
    context = proposal_context
    first_appointment = create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=rounded_future_start,
        service_items=[_service_input(context["service"])],
    )
    first_proposal = create_appointment_proposal(
        actor=context["owner"],
        appointment=first_appointment,
        proposal_type=AppointmentProposal.Type.RESCHEDULE_CONFIRMED,
        options=[AppointmentProposalOptionInput(start_at=_option_start(rounded_future_start))],
    )

    superuser = User.objects.create_superuser(username="other-root", password="secret")
    second = create_partner_with_owner(
        actor=superuser,
        partner_name="Other Proposal Partner",
        owner_username="other-proposal-owner",
        owner_password="secret",
    )
    second_category = create_service_category(
        actor=second.owner,
        partner=second.partner,
        name="Hair",
    )
    second_service = create_service(
        actor=second.owner,
        partner=second.partner,
        category=second_category,
        name="Cut",
        price_amount=30000,
        duration_minutes=45,
    )
    second_client = create_manual_client_profile(
        actor=second.owner,
        partner=second.partner,
        name="Nina",
        phone="+380503333333",
    )
    second_appointment = create_confirmed_appointment(
        actor=second.owner,
        partner=second.partner,
        booking_resource=default_booking_resource_for_partner(second.partner),
        client_profile=second_client,
        start_at=rounded_future_start,
        service_items=[_service_input(second_service)],
    )
    create_appointment_proposal(
        actor=second.owner,
        appointment=second_appointment,
        proposal_type=AppointmentProposal.Type.RESCHEDULE_CONFIRMED,
        options=[AppointmentProposalOptionInput(start_at=_option_start(rounded_future_start))],
    )
    request = RequestFactory().get("/admin/appointments/appointmentproposaloption/")
    request.user = context["owner"]

    queryset = AppointmentProposalOptionAdmin(AppointmentProposalOption, AdminSite()).get_queryset(
        request
    )

    assert list(queryset) == [first_proposal.options.get()]
