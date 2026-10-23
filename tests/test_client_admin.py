from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.appointments.models import Appointment
from apps.appointments.selectors import (
    appointment_history_for_client,
    future_appointments_for_client,
)
from apps.appointments.services import (
    AppointmentServiceItemInput,
    create_confirmed_appointment,
    transition_appointment_status,
)
from apps.catalog.services import create_service, create_service_category
from apps.clients.selectors import client_operational_summary
from apps.clients.services import (
    create_manual_client_profile,
    mark_client_unreachable,
    set_marketing_opt_out,
    update_client_profile,
    upsert_telegram_identity,
)
from apps.finance.models import Penalty
from apps.finance.services import (
    apply_penalties_to_appointment,
    create_penalty,
    mark_penalty_paid,
)
from apps.partners.services import create_partner_with_owner
from apps.schedule.selectors import default_booking_resource_for_partner


@pytest.fixture
def client_admin_context(client):
    superuser = User.objects.create_superuser(username="root", password="secret")
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Client Card Partner",
        owner_username="client-card-owner",
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
    client_profile = create_manual_client_profile(
        actor=created.owner,
        partner=created.partner,
        name="Ira Client",
        phone="+380 50 111 22 33",
    )
    client.force_login(created.owner)
    return {
        "http_client": client,
        "superuser": superuser,
        "created": created,
        "service": service,
        "client_profile": client_profile,
        "resource": default_booking_resource_for_partner(created.partner),
    }


@pytest.mark.django_db
def test_partner_client_card_shows_computed_operational_summary(client_admin_context) -> None:
    context = client_admin_context
    completed = _create_appointment(
        context,
        start_at=timezone.now() - timedelta(days=3),
    )
    transition_appointment_status(
        actor=context["created"].owner,
        appointment=completed,
        new_status=Appointment.Status.COMPLETED,
    )
    future = _create_appointment(
        context,
        start_at=timezone.now() + timedelta(days=3),
    )
    paid_penalty = create_penalty(
        actor=context["created"].owner,
        partner=context["created"].partner,
        client_profile=context["client_profile"],
        reason=Penalty.Reason.LATE_CANCEL,
        amount=5_000,
    )
    mark_penalty_paid(actor=context["created"].owner, penalty=paid_penalty)
    create_penalty(
        actor=context["created"].owner,
        partner=context["created"].partner,
        client_profile=context["client_profile"],
        reason=Penalty.Reason.LATE_ARRIVAL,
        amount=2_000,
    )
    applied_penalty = create_penalty(
        actor=context["created"].owner,
        partner=context["created"].partner,
        client_profile=context["client_profile"],
        reason=Penalty.Reason.NO_SHOW,
        amount=3_000,
    )
    apply_penalties_to_appointment(
        actor=context["created"].owner,
        appointment=future,
        penalties=[applied_penalty],
    )

    summary = client_operational_summary(client_profile=context["client_profile"])

    assert summary.completed_visit_count == 1
    assert summary.future_appointment_count == 1
    assert [(money.amount, money.currency) for money in summary.total_spent] == [(55_000, "UAH")]
    assert summary.outstanding_penalty_count == 2
    assert [(money.amount, money.currency) for money in summary.outstanding_penalty_totals] == [
        (5_000, "UAH")
    ]
    assert list(future_appointments_for_client(client_profile=context["client_profile"])) == [
        future
    ]
    assert list(appointment_history_for_client(client_profile=context["client_profile"])) == [
        completed
    ]

    response = context["http_client"].get(
        reverse(
            "admin:clients_clientprofile_change",
            args=[context["client_profile"].id],
        )
    )
    page = response.content.decode()

    assert response.status_code == 200
    assert "Client overview" in page
    assert "550.00 UAH" in page
    assert "50.00 UAH" in page
    assert "No Telegram connection" in page
    assert "Manicure" in page
    assert "+380501112233" in page


@pytest.mark.django_db
def test_partner_client_card_and_list_stay_tenant_scoped(client_admin_context) -> None:
    context = client_admin_context
    other = create_partner_with_owner(
        actor=context["superuser"],
        partner_name="Other Partner",
        owner_username="other-client-owner",
        owner_password="secret",
    )
    other_client = create_manual_client_profile(
        actor=other.owner,
        partner=other.partner,
        name="Other Tenant Client",
        phone="+380 99 000 11 22",
    )

    list_response = context["http_client"].get(reverse("admin:clients_clientprofile_changelist"))
    foreign_response = context["http_client"].get(
        reverse("admin:clients_clientprofile_change", args=[other_client.id])
    )
    list_page = list_response.content.decode()

    assert list_response.status_code == 200
    assert "Ira Client" in list_page
    assert "***2233" in list_page
    assert "+380501112233" not in list_page
    assert "Other Tenant Client" not in list_page
    assert foreign_response.status_code == 302


@pytest.mark.django_db
def test_owner_updates_only_partner_editable_client_fields(client_admin_context) -> None:
    context = client_admin_context
    profile = context["client_profile"]
    original_partner = profile.partner
    set_marketing_opt_out(client_profile=profile, opt_out=True)
    mark_client_unreachable(client_profile=profile, failure_code="bot_blocked")
    other = create_partner_with_owner(
        actor=context["superuser"],
        partner_name="Forged Partner",
        owner_username="forged-client-owner",
        owner_password="secret",
    )
    identity = upsert_telegram_identity(
        telegram_user_id=9_999_001,
        username="must_not_link",
    )

    response = context["http_client"].post(
        reverse("admin:clients_clientprofile_change", args=[profile.id]),
        {
            "name": "  Updated Client  ",
            "phone": "+38 (093) 444-55-66",
            "language": "en",
            "private_master_note": "  Handle with care.  ",
            "extra_client_text": "  Prefers evenings.  ",
            "partner": str(other.partner.id),
            "telegram_identity": str(identity.id),
            "marketing_opt_out": "",
            "bot_unreachable": "",
            "last_delivery_failure_code": "forged",
            "_save": "Save",
        },
    )

    assert response.status_code == 302
    profile.refresh_from_db()
    assert profile.partner == original_partner
    assert profile.telegram_identity is None
    assert profile.name == "Updated Client"
    assert profile.phone == "+380934445566"
    assert profile.language == "en"
    assert profile.private_master_note == "Handle with care."
    assert profile.extra_client_text == "Prefers evenings."
    assert profile.marketing_opt_out is True
    assert profile.bot_unreachable is True
    assert profile.last_delivery_failure_code == "bot_blocked"


@pytest.mark.django_db
def test_manual_client_admin_rejects_clearing_both_name_and_phone(
    client_admin_context,
) -> None:
    context = client_admin_context
    profile = context["client_profile"]

    response = context["http_client"].post(
        reverse("admin:clients_clientprofile_change", args=[profile.id]),
        {
            "name": "",
            "phone": "",
            "language": "uk",
            "private_master_note": "",
            "extra_client_text": "",
            "_save": "Save",
        },
    )

    assert response.status_code == 200
    assert "Manual client needs at least a name or phone." in response.content.decode()
    profile.refresh_from_db()
    assert profile.name == "Ira Client"
    assert profile.phone == "+380501112233"


@pytest.mark.django_db
def test_client_update_service_rejects_other_partner_owner(client_admin_context) -> None:
    context = client_admin_context
    other = create_partner_with_owner(
        actor=context["superuser"],
        partner_name="Unauthorized Partner",
        owner_username="unauthorized-client-owner",
        owner_password="secret",
    )

    with pytest.raises(PermissionDenied):
        update_client_profile(
            actor=other.owner,
            client_profile=context["client_profile"],
            name="Forbidden",
            phone="",
            language="uk",
        )

    context["client_profile"].refresh_from_db()
    assert context["client_profile"].name == "Ira Client"


@pytest.mark.django_db
def test_client_card_history_is_paginated(client_admin_context) -> None:
    context = client_admin_context
    for offset in range(6):
        appointment = _create_appointment(
            context,
            start_at=timezone.now() - timedelta(days=offset + 1),
        )
        transition_appointment_status(
            actor=context["created"].owner,
            appointment=appointment,
            new_status=Appointment.Status.COMPLETED,
        )

    url = reverse(
        "admin:clients_clientprofile_change",
        args=[context["client_profile"].id],
    )
    first_response = context["http_client"].get(url)
    second_response = context["http_client"].get(url, {"history_page": 2})

    assert first_response.status_code == 200
    assert "Page 1 of 2" in first_response.content.decode()
    assert "Next" in first_response.content.decode()
    assert second_response.status_code == 200
    assert "Page 2 of 2" in second_response.content.decode()
    assert "Previous" in second_response.content.decode()


def _create_appointment(context, *, start_at):
    return create_confirmed_appointment(
        actor=context["created"].owner,
        partner=context["created"].partner,
        booking_resource=context["resource"],
        client_profile=context["client_profile"],
        start_at=start_at,
        service_items=[AppointmentServiceItemInput(service=context["service"])],
    )
