from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.appointments.models import Appointment
from apps.appointments.services import (
    AppointmentServiceItemInput,
    confirm_requested_appointment,
    create_confirmed_appointment,
    create_manual_appointment,
    create_requested_appointment,
    transition_appointment_status,
)
from apps.catalog.services import create_service, create_service_category
from apps.clients.services import create_manual_client_profile
from apps.partners.services import create_partner_with_owner
from apps.schedule.selectors import default_booking_resource_for_partner
from core.database.locks import (
    BOOKING_RESOURCE_ADVISORY_LOCK_NAMESPACE,
    acquire_postgres_advisory_xact_lock,
)


@pytest.fixture
def appointment_context():
    superuser = User.objects.create_superuser(username="root", password="secret")
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Appointments Partner",
        owner_username="appointments-owner",
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
        description="Base care",
    )
    client = create_manual_client_profile(
        actor=created.owner,
        partner=created.partner,
        name="Ira",
        phone="+380501111111",
    )
    return {
        "partner": created.partner,
        "owner": created.owner,
        "resource": default_booking_resource_for_partner(created.partner),
        "category": category,
        "service": service,
        "client": client,
    }


@pytest.fixture
def rounded_future_start():
    return (timezone.now() + timedelta(days=3)).replace(minute=0, second=0, microsecond=0)


def _service_input(service):
    return AppointmentServiceItemInput(service=service)


@pytest.mark.django_db
def test_confirmed_appointments_cannot_overlap_for_one_resource(
    appointment_context,
    rounded_future_start,
):
    context = appointment_context
    create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=rounded_future_start,
        service_items=[_service_input(context["service"])],
    )

    with pytest.raises(ValidationError):
        create_confirmed_appointment(
            actor=context["owner"],
            partner=context["partner"],
            booking_resource=context["resource"],
            client_profile=context["client"],
            start_at=rounded_future_start + timedelta(minutes=30),
            service_items=[_service_input(context["service"])],
        )


@pytest.mark.django_db
def test_requested_appointments_may_overlap_without_blocking_slot(
    appointment_context,
    rounded_future_start,
):
    context = appointment_context

    first = create_requested_appointment(
        actor=None,
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=rounded_future_start,
        service_items=[_service_input(context["service"])],
    )
    second = create_requested_appointment(
        actor=None,
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=rounded_future_start,
        service_items=[_service_input(context["service"])],
    )

    assert first.status == Appointment.Status.REQUESTED
    assert second.status == Appointment.Status.REQUESTED
    assert first.start_at == second.start_at


@pytest.mark.django_db
def test_confirming_requested_appointment_rechecks_overlap(
    appointment_context,
    rounded_future_start,
):
    context = appointment_context
    create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=rounded_future_start,
        service_items=[_service_input(context["service"])],
    )
    requested = create_requested_appointment(
        actor=None,
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=rounded_future_start,
        service_items=[_service_input(context["service"])],
    )

    with pytest.raises(ValidationError):
        confirm_requested_appointment(actor=context["owner"], appointment=requested)


@pytest.mark.django_db
def test_final_appointments_do_not_block_new_confirmed_slots(
    appointment_context,
    rounded_future_start,
):
    context = appointment_context
    appointment = create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=rounded_future_start,
        service_items=[_service_input(context["service"])],
    )
    transition_appointment_status(
        actor=context["owner"],
        appointment=appointment,
        new_status=Appointment.Status.COMPLETED,
    )

    replacement = create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=rounded_future_start,
        service_items=[_service_input(context["service"])],
    )

    assert replacement.status == Appointment.Status.CONFIRMED


@pytest.mark.django_db
def test_appointment_service_item_snapshots_survive_catalog_edits(
    appointment_context,
    rounded_future_start,
):
    context = appointment_context
    appointment = create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=rounded_future_start,
        service_items=[_service_input(context["service"])],
    )
    context["service"].name = "New name"
    context["service"].price_amount = 80000
    context["service"].duration_minutes = 90
    context["service"].save(
        update_fields=["name", "price_amount", "duration_minutes", "updated_at"]
    )

    item = appointment.service_items.get()
    appointment.refresh_from_db()

    assert item.service_name_snapshot == "Manicure"
    assert item.price_amount == 50000
    assert item.duration_minutes == 60
    assert appointment.service_total_amount == 50000
    assert appointment.total_amount == 50000


@pytest.mark.django_db
def test_manual_appointment_can_use_custom_one_off_service(
    appointment_context,
    rounded_future_start,
):
    context = appointment_context

    appointment = create_manual_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=rounded_future_start,
        service_items=[
            AppointmentServiceItemInput(
                service_name="One-off repair",
                price_amount=12300,
                duration_minutes=45,
            )
        ],
        private_master_note="Admin-only",
    )
    item = appointment.service_items.get()

    assert appointment.status == Appointment.Status.CONFIRMED
    assert appointment.source == Appointment.Source.PARTNER_ADMIN
    assert appointment.total_amount == 12300
    assert item.service is None
    assert item.is_custom is True
    assert item.service_name_snapshot == "One-off repair"


@pytest.mark.django_db
def test_confirmed_creation_uses_booking_resource_lock_wrapper(
    appointment_context,
    rounded_future_start,
    monkeypatch,
):
    context = appointment_context
    locked_resource_ids = []

    def fake_lock(booking_resource):
        locked_resource_ids.append(booking_resource.id)

    monkeypatch.setattr(
        "apps.appointments.services.acquire_booking_resource_advisory_lock_if_supported",
        fake_lock,
    )

    create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=rounded_future_start,
        service_items=[_service_input(context["service"])],
    )

    assert locked_resource_ids == [context["resource"].id]


@pytest.mark.django_db
def test_strict_postgres_advisory_lock_helper_fails_on_sqlite():
    with transaction.atomic(), pytest.raises(RuntimeError, match="PostgreSQL"):
        acquire_postgres_advisory_xact_lock(
            namespace=BOOKING_RESOURCE_ADVISORY_LOCK_NAMESPACE,
            lock_key=1,
        )


@pytest.mark.django_db
def test_partner_scope_is_enforced_for_appointment_creation(appointment_context):
    context = appointment_context
    superuser = User.objects.create_superuser(username="other-root", password="secret")
    other = create_partner_with_owner(
        actor=superuser,
        partner_name="Other Partner",
        owner_username="other-owner",
        owner_password="secret",
    )

    with pytest.raises(PermissionDenied):
        create_confirmed_appointment(
            actor=context["owner"],
            partner=other.partner,
            booking_resource=context["resource"],
            client_profile=context["client"],
            start_at=timezone.now() + timedelta(days=4),
            service_items=[_service_input(context["service"])],
        )

    with pytest.raises(ValidationError):
        create_confirmed_appointment(
            actor=None,
            partner=context["partner"],
            booking_resource=default_booking_resource_for_partner(other.partner),
            client_profile=context["client"],
            start_at=timezone.now() + timedelta(days=4),
            service_items=[_service_input(context["service"])],
        )
