from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.appointments.models import (
    Appointment,
    AppointmentProposal,
    AppointmentServiceItem,
)
from apps.appointments.proposals import (
    AppointmentProposalOptionInput,
    create_appointment_proposal,
)
from apps.appointments.services import (
    AppointmentServiceItemInput,
    create_confirmed_appointment,
    create_manual_appointment,
    create_requested_appointment,
    transition_appointment_status,
)
from apps.audit.models import AuditEvent
from apps.catalog.services import create_service, create_service_category
from apps.clients.models import ClientProfile
from apps.clients.services import (
    create_manual_client_profile,
    resolve_client_profile_for_telegram_identity,
    upsert_telegram_identity,
)
from apps.notifications.models import ScheduledNotification
from apps.partners.services import create_partner_with_owner
from apps.schedule.models import BookingResource
from core.timezones import get_zone


@pytest.fixture
def appointment_admin_context(client):
    superuser = User.objects.create_superuser(username="root", password="secret")
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Appointment Admin",
        owner_username="appointment-admin-owner",
        owner_password="secret",
    )
    resource = BookingResource.objects.get(partner=created.partner, is_default=True)
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
    client.force_login(created.owner)
    return client, superuser, created, resource, service


@pytest.mark.django_db
def test_owner_creates_manual_appointment_with_overrides_and_custom_line(
    appointment_admin_context,
) -> None:
    client, _superuser, created, resource, service = appointment_admin_context
    client_profile = create_manual_client_profile(
        actor=created.owner,
        partner=created.partner,
        name="Ira",
        phone="+380 67 111 22 33",
    )
    start_at = _future_hour(days=3)

    response = client.post(
        reverse("admin:appointments_appointment_add"),
        {
            "partner": str(created.partner.id),
            "booking_resource": str(resource.id),
            **_split_datetime_fields("start_at", start_at),
            "client_profile": str(client_profile.id),
            "new_client_name": "",
            "new_client_phone": "",
            "client_comment": "Please use a neutral color.",
            "private_master_note": "First visit.",
            **_service_formset_management(total_forms=2),
            "service_items-0-service": str(service.id),
            "service_items-0-custom_name": "",
            "service_items-0-description": "",
            "service_items-0-price": "550.00",
            "service_items-0-duration_minutes": "75",
            "service_items-1-service": "",
            "service_items-1-custom_name": "Nail repair",
            "service_items-1-description": "One-off repair",
            "service_items-1-price": "100.50",
            "service_items-1-duration_minutes": "15",
            "_save": "Save",
        },
    )

    assert response.status_code == 302
    appointment = Appointment.objects.get(partner=created.partner)
    assert appointment.booking_resource == resource
    assert appointment.client_profile == client_profile
    assert appointment.status == Appointment.Status.CONFIRMED
    assert appointment.source == Appointment.Source.PARTNER_ADMIN
    assert appointment.created_by == created.owner
    assert appointment.start_at == start_at
    assert appointment.end_at == start_at + timedelta(minutes=90)
    assert appointment.service_total_amount == 65_050
    assert appointment.total_amount == 65_050

    service_lines = list(appointment.service_items.order_by("sort_order", "id"))
    assert len(service_lines) == 2
    assert service_lines[0].service == service
    assert service_lines[0].price_amount == 55_000
    assert service_lines[0].duration_minutes == 75
    assert service_lines[0].is_overridden is True
    assert service_lines[1].service is None
    assert service_lines[1].service_name_snapshot == "Nail repair"
    assert service_lines[1].price_amount == 10_050
    assert service_lines[1].is_custom is True


@pytest.mark.django_db
def test_owner_accepts_client_reschedule_request_from_proposal_admin(
    appointment_admin_context,
    django_capture_on_commit_callbacks,
) -> None:
    client, _superuser, created, resource, service = appointment_admin_context
    identity = upsert_telegram_identity(
        telegram_user_id=987654,
        username="reschedule-client",
        first_name="Ira",
    )
    client_profile = resolve_client_profile_for_telegram_identity(
        partner=created.partner,
        telegram_identity=identity,
    )
    start_at = _future_hour(days=3)
    appointment = create_confirmed_appointment(
        actor=created.owner,
        partner=created.partner,
        booking_resource=resource,
        client_profile=client_profile,
        start_at=start_at,
        service_items=[AppointmentServiceItemInput(service=service)],
    )
    proposal = create_appointment_proposal(
        actor=None,
        appointment=appointment,
        proposal_type=AppointmentProposal.Type.RESCHEDULE_CONFIRMED,
        options=[AppointmentProposalOptionInput(start_at=start_at + timedelta(hours=2))],
    )

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            reverse("admin:appointments_appointmentproposal_changelist"),
            {
                "action": "accept_client_reschedule",
                "_selected_action": str(proposal.id),
            },
        )

    assert response.status_code == 302
    appointment.refresh_from_db()
    proposal.refresh_from_db()
    assert appointment.start_at == start_at + timedelta(hours=2)
    assert proposal.status == AppointmentProposal.Status.ACCEPTED
    assert ScheduledNotification.objects.filter(
        appointment_proposal=proposal,
        type=ScheduledNotification.Type.CLIENT_APPOINTMENT_PROPOSAL_ACCEPTED,
    ).exists()


@pytest.mark.django_db
def test_manual_appointment_creates_manual_client_in_same_use_case(
    appointment_admin_context,
) -> None:
    client, _superuser, created, resource, _service = appointment_admin_context
    start_at = _future_hour(days=4)

    response = client.post(
        reverse("admin:appointments_appointment_add"),
        {
            "partner": str(created.partner.id),
            "booking_resource": str(resource.id),
            **_split_datetime_fields("start_at", start_at),
            "client_profile": "",
            "new_client_name": "Manual client",
            "new_client_phone": "+380 93 555 44 11",
            "client_comment": "",
            "private_master_note": "",
            **_service_formset_management(total_forms=1),
            "service_items-0-service": "",
            "service_items-0-custom_name": "Consultation",
            "service_items-0-description": "",
            "service_items-0-price": "0.00",
            "service_items-0-duration_minutes": "30",
            "_save": "Save",
        },
    )

    assert response.status_code == 302
    profile = ClientProfile.objects.get(partner=created.partner, name="Manual client")
    assert profile.phone == "+380935554411"
    assert profile.telegram_identity is None
    appointment = Appointment.objects.get(client_profile=profile)
    item = AppointmentServiceItem.objects.get(appointment=appointment)
    assert appointment.status == Appointment.Status.CONFIRMED
    assert item.service_name_snapshot == "Consultation"
    assert item.price_amount == 0
    assert item.is_custom is True


@pytest.mark.django_db
def test_overlap_rejection_rolls_back_inline_manual_client(
    appointment_admin_context,
) -> None:
    client, _superuser, created, resource, service = appointment_admin_context
    existing_client = create_manual_client_profile(
        actor=created.owner,
        partner=created.partner,
        name="Existing client",
    )
    start_at = _future_hour(days=5)
    create_manual_appointment(
        actor=created.owner,
        partner=created.partner,
        booking_resource=resource,
        client_profile=existing_client,
        start_at=start_at,
        service_items=[AppointmentServiceItemInput(service=service)],
    )

    response = client.post(
        reverse("admin:appointments_appointment_add"),
        {
            "partner": str(created.partner.id),
            "booking_resource": str(resource.id),
            **_split_datetime_fields("start_at", start_at + timedelta(minutes=30)),
            "client_profile": "",
            "new_client_name": "Must roll back",
            "new_client_phone": "",
            "client_comment": "",
            "private_master_note": "",
            **_service_formset_management(total_forms=1),
            "service_items-0-service": str(service.id),
            "service_items-0-custom_name": "",
            "service_items-0-description": "",
            "service_items-0-price": "",
            "service_items-0-duration_minutes": "",
            "_save": "Save",
        },
    )

    assert response.status_code == 200
    assert b"This time is already occupied" in response.content
    assert (
        ClientProfile.objects.filter(
            partner=created.partner,
            name="Must roll back",
        ).exists()
        is False
    )
    assert Appointment.objects.filter(partner=created.partner).count() == 1


@pytest.mark.django_db
def test_owner_cannot_forge_other_partner_manual_appointment_scope(
    appointment_admin_context,
) -> None:
    client, superuser, first, _resource, _service = appointment_admin_context
    second = create_partner_with_owner(
        actor=superuser,
        partner_name="Other Partner",
        owner_username="other-partner-owner",
        owner_password="secret",
    )
    second_resource = BookingResource.objects.get(partner=second.partner, is_default=True)
    second_category = create_service_category(
        actor=second.owner,
        partner=second.partner,
        name="Other",
    )
    second_service = create_service(
        actor=second.owner,
        partner=second.partner,
        category=second_category,
        name="Other service",
        price_amount=10_000,
        duration_minutes=30,
    )

    response = client.post(
        reverse("admin:appointments_appointment_add"),
        {
            "partner": str(second.partner.id),
            "booking_resource": str(second_resource.id),
            **_split_datetime_fields("start_at", _future_hour(days=6)),
            "client_profile": "",
            "new_client_name": "Forged client",
            "new_client_phone": "",
            "client_comment": "",
            "private_master_note": "",
            **_service_formset_management(total_forms=1),
            "service_items-0-service": str(second_service.id),
            "service_items-0-custom_name": "",
            "service_items-0-description": "",
            "service_items-0-price": "",
            "service_items-0-duration_minutes": "",
            "_save": "Save",
        },
    )

    assert response.status_code == 200
    assert Appointment.objects.filter(partner=second.partner).exists() is False
    assert ClientProfile.objects.filter(name="Forged client").exists() is False
    assert Appointment.objects.filter(partner=first.partner).exists() is False


@pytest.mark.django_db
def test_owner_confirms_requested_appointment_from_detail_flow(
    appointment_admin_context,
) -> None:
    client, _superuser, created, resource, service = appointment_admin_context
    client_profile = create_manual_client_profile(
        actor=created.owner,
        partner=created.partner,
        name="Requested client",
    )
    appointment = create_requested_appointment(
        actor=created.owner,
        partner=created.partner,
        booking_resource=resource,
        client_profile=client_profile,
        start_at=_future_hour(days=2),
        service_items=[AppointmentServiceItemInput(service=service)],
    )
    status_url = reverse(
        "admin:appointments_appointment_status",
        args=[appointment.id],
    )

    page = client.get(status_url)
    response = client.post(
        status_url,
        {
            "new_status": Appointment.Status.CONFIRMED,
            "reason": "",
        },
    )

    assert page.status_code == 200
    assert b'option value="confirmed"' in page.content
    assert b'option value="cancelled_by_master"' in page.content
    assert b'option value="completed"' not in page.content
    assert response.status_code == 302
    appointment.refresh_from_db()
    assert appointment.status == Appointment.Status.CONFIRMED
    assert AuditEvent.objects.filter(
        partner=created.partner,
        target_id=str(appointment.id),
        event_type="appointment.status_changed",
        payload_masked__new_status=Appointment.Status.CONFIRMED,
    ).exists()


@pytest.mark.django_db
def test_owner_proposes_multiple_times_through_service_backed_admin_flow(
    appointment_admin_context,
    django_capture_on_commit_callbacks,
) -> None:
    client, _superuser, created, resource, service = appointment_admin_context
    identity = upsert_telegram_identity(
        telegram_user_id=901001,
        username="proposal-client",
        first_name="Ira",
    )
    client_profile = resolve_client_profile_for_telegram_identity(
        partner=created.partner,
        telegram_identity=identity,
    )
    appointment = create_confirmed_appointment(
        actor=created.owner,
        partner=created.partner,
        booking_resource=resource,
        client_profile=client_profile,
        start_at=_future_hour(days=2),
        service_items=[AppointmentServiceItemInput(service=service)],
    )
    first_option = _future_hour(days=3)
    second_option = _future_hour(days=4)
    proposal_url = reverse(
        "admin:appointments_appointment_propose_time",
        args=[appointment.id],
    )

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            proposal_url,
            {
                "message": "Choose whichever time works better.",
                "reason": "Schedule changed.",
                **_proposal_formset_management(total_forms=2),
                **_split_datetime_fields("options-0-start_at", first_option),
                **_split_datetime_fields("options-1-start_at", second_option),
            },
        )

    assert response.status_code == 302
    proposal = AppointmentProposal.objects.get(appointment=appointment)
    assert proposal.type == AppointmentProposal.Type.RESCHEDULE_CONFIRMED
    assert proposal.status == AppointmentProposal.Status.PENDING
    assert proposal.message == "Choose whichever time works better."
    assert list(proposal.options.values_list("start_at", flat=True)) == [
        first_option,
        second_option,
    ]
    assert ScheduledNotification.objects.filter(
        appointment_proposal=proposal,
        type=ScheduledNotification.Type.CLIENT_APPOINTMENT_PROPOSAL,
        status=ScheduledNotification.Status.QUEUED,
    ).exists()
    assert AuditEvent.objects.filter(
        partner=created.partner,
        target_id=str(proposal.id),
        event_type="appointment.proposal_created",
    ).exists()
    detail_response = client.get(response["Location"])
    expected_partner_time = first_option.astimezone(get_zone(created.partner.timezone)).strftime(
        "%Y-%m-%d %H:%M %Z"
    )
    assert detail_response.status_code == 200
    assert expected_partner_time in detail_response.content.decode()


@pytest.mark.django_db
def test_proposal_admin_flow_requires_client_telegram_connection(
    appointment_admin_context,
) -> None:
    client, _superuser, created, resource, service = appointment_admin_context
    client_profile = create_manual_client_profile(
        actor=created.owner,
        partner=created.partner,
        name="Offline client",
    )
    appointment = create_confirmed_appointment(
        actor=created.owner,
        partner=created.partner,
        booking_resource=resource,
        client_profile=client_profile,
        start_at=_future_hour(days=2),
        service_items=[AppointmentServiceItemInput(service=service)],
    )

    response = client.get(
        reverse(
            "admin:appointments_appointment_propose_time",
            args=[appointment.id],
        )
    )

    assert response.status_code == 302
    assert AppointmentProposal.objects.filter(appointment=appointment).exists() is False


@pytest.mark.django_db
def test_proposal_admin_flow_is_tenant_scoped(appointment_admin_context) -> None:
    client, superuser, created, _resource, _service = appointment_admin_context
    other = create_partner_with_owner(
        actor=superuser,
        partner_name="Other Appointment Admin",
        owner_username="other-appointment-admin-owner",
        owner_password="secret",
    )
    other_resource = BookingResource.objects.get(partner=other.partner, is_default=True)
    other_category = create_service_category(
        actor=other.owner,
        partner=other.partner,
        name="Other services",
    )
    other_service = create_service(
        actor=other.owner,
        partner=other.partner,
        category=other_category,
        name="Other manicure",
        price_amount=40_000,
        duration_minutes=60,
    )
    identity = upsert_telegram_identity(
        telegram_user_id=901002,
        username="other-proposal-client",
        first_name="Other",
    )
    other_client = resolve_client_profile_for_telegram_identity(
        partner=other.partner,
        telegram_identity=identity,
    )
    other_appointment = create_confirmed_appointment(
        actor=other.owner,
        partner=other.partner,
        booking_resource=other_resource,
        client_profile=other_client,
        start_at=_future_hour(days=5),
        service_items=[AppointmentServiceItemInput(service=other_service)],
    )

    response = client.get(
        reverse(
            "admin:appointments_appointment_propose_time",
            args=[other_appointment.id],
        )
    )

    assert response.status_code == 404
    assert AppointmentProposal.objects.filter(appointment=other_appointment).exists() is False


@pytest.mark.django_db
def test_owner_completes_and_corrects_past_appointment_from_detail_flow(
    appointment_admin_context,
) -> None:
    client, _superuser, created, resource, service = appointment_admin_context
    client_profile = create_manual_client_profile(
        actor=created.owner,
        partner=created.partner,
        name="Completed client",
    )
    appointment = create_confirmed_appointment(
        actor=created.owner,
        partner=created.partner,
        booking_resource=resource,
        client_profile=client_profile,
        start_at=_past_hour(hours=2),
        service_items=[AppointmentServiceItemInput(service=service)],
    )
    status_url = reverse(
        "admin:appointments_appointment_status",
        args=[appointment.id],
    )

    completed_response = client.post(
        status_url,
        {"new_status": Appointment.Status.COMPLETED, "reason": ""},
    )
    missing_reason_response = client.post(
        status_url,
        {"new_status": Appointment.Status.NO_SHOW, "reason": ""},
    )
    corrected_response = client.post(
        status_url,
        {
            "new_status": Appointment.Status.NO_SHOW,
            "reason": "Marked completed by mistake.",
        },
    )

    assert completed_response.status_code == 302
    assert missing_reason_response.status_code == 200
    assert "reason" in missing_reason_response.context["form"].errors
    assert corrected_response.status_code == 302
    appointment.refresh_from_db()
    assert appointment.status == Appointment.Status.NO_SHOW
    assert appointment.completed_at is None
    assert appointment.no_show_at is not None
    assert appointment.last_correction_reason == "Marked completed by mistake."
    assert AuditEvent.objects.filter(
        partner=created.partner,
        target_id=str(appointment.id),
        event_type="appointment.status_corrected",
        reason="Marked completed by mistake.",
    ).exists()


@pytest.mark.django_db
def test_future_appointment_cannot_be_completed_or_marked_no_show(
    appointment_admin_context,
) -> None:
    client, _superuser, created, resource, service = appointment_admin_context
    client_profile = create_manual_client_profile(
        actor=created.owner,
        partner=created.partner,
        name="Future client",
    )
    appointment = create_confirmed_appointment(
        actor=created.owner,
        partner=created.partner,
        booking_resource=resource,
        client_profile=client_profile,
        start_at=_future_hour(days=2),
        service_items=[AppointmentServiceItemInput(service=service)],
    )
    status_url = reverse(
        "admin:appointments_appointment_status",
        args=[appointment.id],
    )

    page = client.get(status_url)

    assert page.status_code == 200
    assert b'option value="cancelled_by_master"' in page.content
    assert b'option value="completed"' not in page.content
    assert b'option value="no_show"' not in page.content
    with pytest.raises(ValidationError, match="only after it starts"):
        transition_appointment_status(
            actor=created.owner,
            appointment=appointment,
            new_status=Appointment.Status.COMPLETED,
        )


@pytest.mark.django_db
def test_status_flow_does_not_expose_other_partner_appointment(
    appointment_admin_context,
) -> None:
    client, superuser, created, resource, service = appointment_admin_context
    client_profile = create_manual_client_profile(
        actor=created.owner,
        partner=created.partner,
        name="Private client",
    )
    appointment = create_requested_appointment(
        actor=created.owner,
        partner=created.partner,
        booking_resource=resource,
        client_profile=client_profile,
        start_at=_future_hour(days=3),
        service_items=[AppointmentServiceItemInput(service=service)],
    )
    other = create_partner_with_owner(
        actor=superuser,
        partner_name="Unrelated Partner",
        owner_username="unrelated-status-owner",
        owner_password="secret",
    )
    client.force_login(other.owner)

    response = client.get(reverse("admin:appointments_appointment_status", args=[appointment.id]))

    assert response.status_code == 404


def _future_hour(*, days: int):
    return timezone.localtime(
        timezone.now() + timedelta(days=days),
        get_zone("Europe/Kiev"),
    ).replace(
        minute=0,
        second=0,
        microsecond=0,
    )


def _past_hour(*, hours: int):
    return timezone.localtime(
        timezone.now() - timedelta(hours=hours),
        get_zone("Europe/Kiev"),
    ).replace(
        minute=0,
        second=0,
        microsecond=0,
    )


def _split_datetime_fields(prefix: str, value) -> dict[str, str]:
    return {
        f"{prefix}_0": value.date().isoformat(),
        f"{prefix}_1": value.time().isoformat(),
    }


def _service_formset_management(*, total_forms: int) -> dict[str, str]:
    return {
        "service_items-TOTAL_FORMS": str(total_forms),
        "service_items-INITIAL_FORMS": "0",
        "service_items-MIN_NUM_FORMS": "0",
        "service_items-MAX_NUM_FORMS": "1000",
    }


def _proposal_formset_management(*, total_forms: int) -> dict[str, str]:
    return {
        "options-TOTAL_FORMS": str(total_forms),
        "options-INITIAL_FORMS": "0",
        "options-MIN_NUM_FORMS": "0",
        "options-MAX_NUM_FORMS": "1000",
    }
