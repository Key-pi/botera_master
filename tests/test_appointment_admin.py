from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.appointments.models import Appointment, AppointmentServiceItem
from apps.appointments.services import AppointmentServiceItemInput, create_manual_appointment
from apps.catalog.services import create_service, create_service_category
from apps.clients.models import ClientProfile
from apps.clients.services import create_manual_client_profile
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


def _future_hour(*, days: int):
    return timezone.localtime(
        timezone.now() + timedelta(days=days),
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
