from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.appointments.models import Appointment
from apps.appointments.services import (
    AppointmentServiceItemInput,
    create_confirmed_appointment,
)
from apps.audit.models import AuditEvent
from apps.catalog.services import create_service, create_service_category
from apps.clients.services import create_manual_client_profile
from apps.finance.models import Expense, PartnerPenaltySettings, Penalty, PenaltyApplication
from apps.finance.services import (
    apply_penalties_to_appointment,
    create_expense,
    create_penalty,
    update_penalty_settings,
)
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

    change_page = context["http_client"].get(change_url)

    response = context["http_client"].post(
        change_url,
        {
            "penalties_enabled": "on",
            "late_cancel_penalty": "65.00",
            "no_show_penalty": "75.00",
            "late_arrival_threshold_minutes": "20",
            "late_arrival_penalty": "25.00",
            "_save": "Save",
        },
    )

    assert change_page.status_code == 200
    settings_form = change_page.context["adminform"].form
    assert settings_form["late_cancel_penalty"].value() == Decimal("50.00")
    assert settings_form["no_show_penalty"].value() == Decimal("50.00")
    assert settings_form["late_arrival_penalty"].value() == Decimal("20.00")
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
def test_no_show_status_redirects_to_optional_configured_penalty(
    finance_admin_context,
) -> None:
    context = finance_admin_context
    update_penalty_settings(
        actor=context["created"].owner,
        partner=context["created"].partner,
        penalties_enabled=True,
        no_show_penalty_amount=7_500,
    )
    appointment = _create_appointment(
        context,
        start_at=timezone.now() - timedelta(hours=2),
    )
    status_url = reverse(
        "admin:appointments_appointment_status",
        args=[appointment.id],
    )
    penalty_url = reverse(
        "admin:appointments_appointment_no_show_penalty",
        args=[appointment.id],
    )
    change_url = reverse(
        "admin:appointments_appointment_change",
        args=[appointment.id],
    )

    status_response = context["http_client"].post(
        status_url,
        {"new_status": Appointment.Status.NO_SHOW, "reason": ""},
    )
    suggestion_response = context["http_client"].get(penalty_url)
    create_response = context["http_client"].post(
        penalty_url,
        {"note": "Missed appointment"},
    )
    duplicate_response = context["http_client"].post(
        penalty_url,
        {"note": "Duplicate submit"},
    )

    assert status_response.status_code == 302
    assert status_response.url == penalty_url
    assert suggestion_response.status_code == 200
    assert "75.00 UAH" in suggestion_response.content.decode()
    assert create_response.status_code == 302
    assert create_response.url == change_url
    assert duplicate_response.status_code == 302
    penalties = Penalty.objects.filter(
        source_appointment=appointment,
        reason=Penalty.Reason.NO_SHOW,
    )
    assert penalties.count() == 1
    penalty = penalties.get()
    assert penalty.amount == 7_500
    assert penalty.note == "Missed appointment"


@pytest.mark.django_db
def test_owner_records_late_arrival_from_appointment_detail(
    finance_admin_context,
) -> None:
    context = finance_admin_context
    update_penalty_settings(
        actor=context["created"].owner,
        partner=context["created"].partner,
        penalties_enabled=True,
        late_arrival_threshold_minutes=20,
        late_arrival_penalty_amount=2_500,
    )
    appointment = _create_appointment(
        context,
        start_at=timezone.now() - timedelta(hours=1),
    )
    change_url = reverse(
        "admin:appointments_appointment_change",
        args=[appointment.id],
    )
    action_url = reverse(
        "admin:appointments_appointment_late_arrival_penalty",
        args=[appointment.id],
    )

    change_response = context["http_client"].get(change_url)
    action_response = context["http_client"].get(action_url)
    below_threshold_response = context["http_client"].post(
        action_url,
        {"late_by_minutes": "19", "note": ""},
    )
    create_response = context["http_client"].post(
        action_url,
        {"late_by_minutes": "20", "note": "Traffic"},
    )

    assert change_response.status_code == 200
    assert action_url in change_response.content.decode()
    assert action_response.status_code == 200
    assert "25.00 UAH" in action_response.content.decode()
    assert "20 minutes" in action_response.content.decode()
    assert below_threshold_response.status_code == 200
    assert "late_by_minutes" in below_threshold_response.context["form"].errors
    assert create_response.status_code == 302
    assert create_response.url == change_url
    penalty = Penalty.objects.get(
        source_appointment=appointment,
        reason=Penalty.Reason.LATE_ARRIVAL,
    )
    assert penalty.amount == 2_500
    assert penalty.late_by_minutes == 20
    assert penalty.note == "Traffic"


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


@pytest.mark.django_db
def test_owner_manages_expenses_in_major_units_with_audit(
    finance_admin_context,
) -> None:
    context = finance_admin_context
    partner = context["created"].partner
    owner = context["created"].owner
    expense_date = timezone.localdate()
    add_url = reverse("admin:finance_expense_add")

    create_response = context["http_client"].post(
        add_url,
        {
            "partner": str(partner.id),
            "date": expense_date.isoformat(),
            "amount_major": "100.50",
            "currency": "uah",
            "description": "  Materials  ",
            "_save": "Save",
        },
    )

    assert create_response.status_code == 302
    expense = Expense.objects.get(partner=partner)
    assert expense.amount == 10_050
    assert expense.currency == "UAH"
    assert expense.description == "Materials"
    assert expense.created_by == owner
    assert AuditEvent.objects.filter(
        partner=partner,
        actor=owner,
        target_id=str(expense.id),
        event_type="expense.created",
    ).exists()

    change_url = reverse("admin:finance_expense_change", args=[expense.id])
    change_page = context["http_client"].get(change_url)
    update_response = context["http_client"].post(
        change_url,
        {
            "date": expense_date.isoformat(),
            "amount_major": "125.25",
            "currency": "UAH",
            "description": "Materials and tools",
            "_save": "Save",
        },
    )

    assert change_page.status_code == 200
    assert change_page.context["adminform"].form["amount_major"].value() == Decimal("100.50")
    assert update_response.status_code == 302
    expense.refresh_from_db()
    assert expense.amount == 12_525
    assert expense.description == "Materials and tools"
    assert expense.created_by == owner
    assert AuditEvent.objects.filter(
        partner=partner,
        actor=owner,
        target_id=str(expense.id),
        event_type="expense.updated",
    ).exists()

    delete_url = reverse("admin:finance_expense_delete", args=[expense.id])
    delete_response = context["http_client"].post(delete_url, {"post": "yes"})

    assert delete_response.status_code == 302
    assert not Expense.objects.filter(id=expense.id).exists()
    assert AuditEvent.objects.filter(
        partner=partner,
        actor=owner,
        target_id=str(expense.id),
        event_type="expense.deleted",
    ).exists()


@pytest.mark.django_db
def test_expense_admin_is_tenant_scoped(finance_admin_context) -> None:
    context = finance_admin_context
    other = create_partner_with_owner(
        actor=context["superuser"],
        partner_name="Foreign Expense Partner",
        owner_username="foreign-expense-owner",
        owner_password="secret",
    )
    own_expense = create_expense(
        actor=context["created"].owner,
        partner=context["created"].partner,
        date=timezone.localdate(),
        amount=2_000,
        description="Own supplies",
    )
    foreign_expense = create_expense(
        actor=other.owner,
        partner=other.partner,
        date=timezone.localdate(),
        amount=3_000,
        description="Foreign supplies",
    )

    response = context["http_client"].get(reverse("admin:finance_expense_changelist"))
    foreign_change_response = context["http_client"].get(
        reverse("admin:finance_expense_change", args=[foreign_expense.id])
    )
    content = response.content.decode()

    assert response.status_code == 200
    assert "Own supplies" in content
    assert "Foreign supplies" not in content
    assert foreign_change_response.status_code in {302, 404}
    assert Expense.objects.filter(id=own_expense.id).exists()
    assert Expense.objects.filter(id=foreign_expense.id).exists()


def _create_appointment(context, *, start_at=None):
    return create_confirmed_appointment(
        actor=context["created"].owner,
        partner=context["created"].partner,
        booking_resource=context["resource"],
        client_profile=context["client_profile"],
        start_at=start_at or timezone.now() + timedelta(days=3),
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
