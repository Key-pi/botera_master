from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.appointments.services import AppointmentServiceItemInput, create_confirmed_appointment
from apps.audit.models import AuditEvent
from apps.catalog.services import create_service, create_service_category
from apps.clients.services import create_manual_client_profile
from apps.finance.models import PartnerPenaltySettings, Penalty, PenaltyApplication
from apps.finance.services import apply_penalties_to_appointment, create_penalty
from apps.partners.services import create_partner_with_owner
from apps.schedule.selectors import default_booking_resource_for_partner


@pytest.fixture
def finance_admin_context(client):
    superuser = User.objects.create_superuser(username="root", password="secret")
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Finance Admin Partner",
        owner_username="finance-admin-owner",
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
        phone="+380501112233",
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
def test_owner_marks_penalty_paid_from_client_card(finance_admin_context) -> None:
    context = finance_admin_context
    penalty = _create_penalty(context, reason=Penalty.Reason.LATE_CANCEL, amount=5_000)
    card_url = reverse(
        "admin:clients_clientprofile_change",
        args=[context["client_profile"].id],
    )
    action_url = reverse(
        "admin:clients_clientprofile_penalty_action",
        args=[context["client_profile"].id, penalty.id],
    )

    card_response = context["http_client"].get(card_url)
    action_response = context["http_client"].get(action_url)
    response = context["http_client"].post(
        action_url,
        {"action": "mark_paid", "note": ""},
    )

    assert card_response.status_code == 200
    assert action_url in card_response.content.decode()
    assert action_response.status_code == 200
    assert action_response.context["penalty"] == penalty
    assert "50.00 UAH" in action_response.content.decode()
    assert response.status_code == 302
    assert response.url == f"{card_url}#outstanding-penalties"
    penalty.refresh_from_db()
    assert penalty.status == Penalty.Status.PAID
    assert AuditEvent.objects.filter(
        partner=context["created"].partner,
        target_id=str(penalty.id),
        event_type="penalty.paid",
        actor=context["created"].owner,
    ).exists()


@pytest.mark.django_db
def test_owner_waives_applied_penalty_and_restores_appointment_total(
    finance_admin_context,
) -> None:
    context = finance_admin_context
    appointment = _create_appointment(context)
    penalty = _create_penalty(context, reason=Penalty.Reason.NO_SHOW, amount=5_000)
    application = apply_penalties_to_appointment(
        actor=context["created"].owner,
        appointment=appointment,
        penalties=[penalty],
    )[0]
    action_url = reverse(
        "admin:clients_clientprofile_penalty_action",
        args=[context["client_profile"].id, penalty.id],
    )

    response = context["http_client"].post(
        action_url,
        {"action": "waive", "note": "One-time exception"},
    )

    assert response.status_code == 302
    penalty.refresh_from_db()
    application.refresh_from_db()
    appointment.refresh_from_db()
    assert penalty.status == Penalty.Status.WAIVED
    assert penalty.note == "One-time exception"
    assert application.status == PenaltyApplication.Status.RELEASED
    assert appointment.penalties_total_amount == 0
    assert appointment.total_amount == 50_000
    assert AuditEvent.objects.filter(
        target_id=str(penalty.id),
        event_type="penalty.waived",
        reason="One-time exception",
    ).exists()


@pytest.mark.django_db
def test_owner_applies_selected_penalty_from_appointment_detail(
    finance_admin_context,
) -> None:
    context = finance_admin_context
    appointment = _create_appointment(context)
    penalty = _create_penalty(context, reason=Penalty.Reason.LATE_CANCEL, amount=5_000)
    change_url = reverse(
        "admin:appointments_appointment_change",
        args=[appointment.id],
    )
    action_url = reverse(
        "admin:appointments_appointment_penalties",
        args=[appointment.id],
    )

    change_response = context["http_client"].get(change_url)
    action_response = context["http_client"].get(action_url)
    response = context["http_client"].post(
        action_url,
        {"penalties": [str(penalty.id)]},
    )

    assert change_response.status_code == 200
    assert action_url in change_response.content.decode()
    assert action_response.status_code == 200
    assert "Late cancellation - 50.00 UAH" in action_response.content.decode()
    assert response.status_code == 302
    assert response.url == change_url
    penalty.refresh_from_db()
    appointment.refresh_from_db()
    assert penalty.status == Penalty.Status.APPLIED_TO_APPOINTMENT
    assert appointment.penalties_total_amount == 5_000
    assert appointment.total_amount == 55_000
    assert PenaltyApplication.objects.filter(
        penalty=penalty,
        appointment=appointment,
        status=PenaltyApplication.Status.ACTIVE,
    ).exists()


@pytest.mark.django_db
def test_owner_updates_penalty_settings_through_service_backed_admin(
    finance_admin_context,
) -> None:
    context = finance_admin_context
    settings = PartnerPenaltySettings.objects.get(partner=context["created"].partner)
    change_url = reverse(
        "admin:finance_partnerpenaltysettings_change",
        args=[settings.id],
    )

    response = context["http_client"].post(
        change_url,
        {
            "penalties_enabled": "on",
            "late_cancel_penalty_amount": "6500",
            "no_show_penalty_amount": "7500",
            "late_arrival_threshold_minutes": "20",
            "late_arrival_penalty_amount": "2500",
            "_save": "Save",
        },
    )

    assert response.status_code == 302
    settings.refresh_from_db()
    assert settings.penalties_enabled is True
    assert settings.late_cancel_penalty_amount == 6_500
    assert settings.no_show_penalty_amount == 7_500
    assert settings.late_arrival_threshold_minutes == 20
    assert settings.late_arrival_penalty_amount == 2_500
    assert settings.currency == "UAH"
    assert AuditEvent.objects.filter(
        partner=context["created"].partner,
        target_id=str(settings.id),
        event_type="penalty.settings_updated",
        actor=context["created"].owner,
    ).exists()


@pytest.mark.django_db
def test_penalty_admin_operations_reject_foreign_tenant_and_client_ids(
    finance_admin_context,
) -> None:
    context = finance_admin_context
    appointment = _create_appointment(context)
    second_client = create_manual_client_profile(
        actor=context["created"].owner,
        partner=context["created"].partner,
        name="Other Client",
    )
    wrong_client_penalty = create_penalty(
        actor=context["created"].owner,
        partner=context["created"].partner,
        client_profile=second_client,
        reason=Penalty.Reason.NO_SHOW,
        amount=5_000,
    )
    other = create_partner_with_owner(
        actor=context["superuser"],
        partner_name="Foreign Finance Partner",
        owner_username="foreign-finance-owner",
        owner_password="secret",
    )
    foreign_client = create_manual_client_profile(
        actor=other.owner,
        partner=other.partner,
        name="Foreign Client",
    )
    foreign_penalty = create_penalty(
        actor=other.owner,
        partner=other.partner,
        client_profile=foreign_client,
        reason=Penalty.Reason.LATE_CANCEL,
        amount=5_000,
    )

    foreign_action_response = context["http_client"].get(
        reverse(
            "admin:clients_clientprofile_penalty_action",
            args=[context["client_profile"].id, foreign_penalty.id],
        )
    )
    forged_apply_response = context["http_client"].post(
        reverse("admin:appointments_appointment_penalties", args=[appointment.id]),
        {"penalties": [str(wrong_client_penalty.id)]},
    )

    assert foreign_action_response.status_code == 404
    assert forged_apply_response.status_code == 200
    form_errors = forged_apply_response.context["form"].errors.as_data()
    assert form_errors["penalties"][0].code == "invalid_choice"
    wrong_client_penalty.refresh_from_db()
    appointment.refresh_from_db()
    assert wrong_client_penalty.status == Penalty.Status.UNPAID
    assert appointment.penalties_total_amount == 0


def _create_appointment(context):
    return create_confirmed_appointment(
        actor=context["created"].owner,
        partner=context["created"].partner,
        booking_resource=context["resource"],
        client_profile=context["client_profile"],
        start_at=timezone.now() + timedelta(days=3),
        service_items=[AppointmentServiceItemInput(service=context["service"])],
    )


def _create_penalty(context, *, reason: str, amount: int) -> Penalty:
    return create_penalty(
        actor=context["created"].owner,
        partner=context["created"].partner,
        client_profile=context["client_profile"],
        reason=reason,
        amount=amount,
    )
