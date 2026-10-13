from datetime import date, datetime, timedelta
from datetime import timezone as datetime_timezone

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.appointments.models import Appointment
from apps.appointments.services import (
    AppointmentServiceItemInput,
    create_confirmed_appointment,
    transition_appointment_status,
)
from apps.catalog.services import create_service, create_service_category
from apps.clients.services import create_manual_client_profile
from apps.finance.models import Expense, Penalty, PenaltyApplication
from apps.finance.selectors import finance_report_for_partner
from apps.finance.services import (
    apply_penalties_to_appointment,
    create_expense,
    create_late_arrival_penalty_for_appointment,
    create_late_cancel_penalty_for_appointment,
    create_no_show_penalty_for_appointment,
    create_penalty,
    release_penalty_application,
    settle_penalty_application,
    update_penalty_settings,
    waive_penalty,
)
from apps.partners.services import create_partner_with_owner
from apps.schedule.selectors import default_booking_resource_for_partner
from apps.schedule.services import update_schedule_settings


@pytest.fixture
def finance_context():
    superuser = User.objects.create_superuser(username="root", password="secret")
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Finance Partner",
        owner_username="finance-owner",
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
        "service": service,
        "client": client,
    }


def _service_input(service):
    return AppointmentServiceItemInput(service=service)


def _confirmed(context, start_at):
    return create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=start_at,
        service_items=[_service_input(context["service"])],
    )


@pytest.mark.django_db
def test_apply_and_settle_penalty_updates_statuses_and_appointment_total(finance_context):
    context = finance_context
    appointment = _confirmed(context, timezone.now() + timedelta(days=2))
    penalty = create_penalty(
        actor=context["owner"],
        partner=context["partner"],
        client_profile=context["client"],
        reason=Penalty.Reason.LATE_CANCEL,
        amount=5000,
    )

    application = apply_penalties_to_appointment(
        actor=context["owner"],
        appointment=appointment,
        penalties=[penalty],
    )[0]
    appointment.refresh_from_db()
    penalty.refresh_from_db()

    assert penalty.status == Penalty.Status.APPLIED_TO_APPOINTMENT
    assert appointment.penalties_total_amount == 5000
    assert appointment.total_amount == 55000

    settle_penalty_application(actor=context["owner"], application=application)
    application.refresh_from_db()
    penalty.refresh_from_db()
    appointment.refresh_from_db()

    assert application.status == PenaltyApplication.Status.SETTLED
    assert penalty.status == Penalty.Status.PAID
    assert appointment.total_amount == 55000


@pytest.mark.django_db
def test_cancelling_appointment_releases_active_penalty_application(finance_context):
    context = finance_context
    appointment = _confirmed(context, timezone.now() + timedelta(days=2))
    penalty = create_penalty(
        actor=context["owner"],
        partner=context["partner"],
        client_profile=context["client"],
        reason=Penalty.Reason.NO_SHOW,
        amount=5000,
    )
    application = apply_penalties_to_appointment(
        actor=context["owner"],
        appointment=appointment,
        penalties=[penalty],
    )[0]

    transition_appointment_status(
        actor=context["owner"],
        appointment=appointment,
        new_status=Appointment.Status.CANCELLED_BY_MASTER,
    )
    application.refresh_from_db()
    penalty.refresh_from_db()
    appointment.refresh_from_db()

    assert application.status == PenaltyApplication.Status.RELEASED
    assert penalty.status == Penalty.Status.UNPAID
    assert appointment.penalties_total_amount == 0
    assert appointment.total_amount == 50000


@pytest.mark.django_db
def test_manual_release_and_waive_flows_restore_totals(finance_context):
    context = finance_context
    appointment = _confirmed(context, timezone.now() + timedelta(days=2))
    first = create_penalty(
        actor=context["owner"],
        partner=context["partner"],
        client_profile=context["client"],
        reason=Penalty.Reason.LATE_CANCEL,
        amount=5000,
    )
    first_application = apply_penalties_to_appointment(
        actor=context["owner"],
        appointment=appointment,
        penalties=[first],
    )[0]

    release_penalty_application(actor=context["owner"], application=first_application)
    first.refresh_from_db()
    appointment.refresh_from_db()

    assert first.status == Penalty.Status.UNPAID
    assert appointment.total_amount == 50000

    second = create_penalty(
        actor=context["owner"],
        partner=context["partner"],
        client_profile=context["client"],
        reason=Penalty.Reason.NO_SHOW,
        amount=5000,
    )
    apply_penalties_to_appointment(
        actor=context["owner"],
        appointment=appointment,
        penalties=[second],
    )
    waive_penalty(actor=context["owner"], penalty=second, note="Forgiven")
    second.refresh_from_db()
    appointment.refresh_from_db()

    assert second.status == Penalty.Status.WAIVED
    assert second.note == "Forgiven"
    assert appointment.total_amount == 50000


@pytest.mark.django_db
def test_configured_late_cancel_no_show_and_late_arrival_penalties(finance_context):
    context = finance_context
    update_penalty_settings(
        actor=context["owner"],
        partner=context["partner"],
        penalties_enabled=True,
    )
    update_schedule_settings(
        actor=context["owner"],
        partner=context["partner"],
        cancellation_deadline_hours=6,
    )

    late_cancel_appointment = _confirmed(context, timezone.now() + timedelta(hours=2))
    transition_appointment_status(
        actor=context["owner"],
        appointment=late_cancel_appointment,
        new_status=Appointment.Status.CANCELLED_BY_CLIENT,
    )
    late_cancel_appointment.refresh_from_db()
    late_cancel = create_late_cancel_penalty_for_appointment(
        actor=context["owner"],
        appointment=late_cancel_appointment,
    )

    no_show_appointment = _confirmed(context, timezone.now() + timedelta(days=3))
    transition_appointment_status(
        actor=context["owner"],
        appointment=no_show_appointment,
        new_status=Appointment.Status.NO_SHOW,
    )
    no_show_appointment.refresh_from_db()
    no_show = create_no_show_penalty_for_appointment(
        actor=context["owner"],
        appointment=no_show_appointment,
    )

    late_arrival_appointment = _confirmed(context, timezone.now() + timedelta(days=4))
    with pytest.raises(ValidationError):
        create_late_arrival_penalty_for_appointment(
            actor=context["owner"],
            appointment=late_arrival_appointment,
            late_by_minutes=10,
        )
    late_arrival = create_late_arrival_penalty_for_appointment(
        actor=context["owner"],
        appointment=late_arrival_appointment,
        late_by_minutes=15,
    )

    assert late_cancel.reason == Penalty.Reason.LATE_CANCEL
    assert late_cancel.amount == 5000
    assert no_show.reason == Penalty.Reason.NO_SHOW
    assert no_show.amount == 5000
    assert late_arrival.reason == Penalty.Reason.LATE_ARRIVAL
    assert late_arrival.amount == 2000


@pytest.mark.django_db
def test_finance_report_uses_partner_timezone_and_separates_penalties_and_expenses(
    finance_context,
):
    context = finance_context
    context["partner"].timezone = "Europe/Kiev"
    context["partner"].save(update_fields=["timezone", "updated_at"])
    completed_start = datetime(2026, 1, 1, 22, 30, tzinfo=datetime_timezone.utc)
    completed = _confirmed(context, completed_start)
    completed_penalty = create_penalty(
        actor=context["owner"],
        partner=context["partner"],
        client_profile=context["client"],
        reason=Penalty.Reason.LATE_CANCEL,
        amount=5000,
    )
    completed_application = apply_penalties_to_appointment(
        actor=context["owner"],
        appointment=completed,
        penalties=[completed_penalty],
    )[0]
    settle_penalty_application(actor=context["owner"], application=completed_application)
    transition_appointment_status(
        actor=context["owner"],
        appointment=completed,
        new_status=Appointment.Status.COMPLETED,
    )

    expected_start = datetime(2026, 1, 2, 8, 0, tzinfo=datetime_timezone.utc)
    expected = _confirmed(context, expected_start)
    expected_penalty = create_penalty(
        actor=context["owner"],
        partner=context["partner"],
        client_profile=context["client"],
        reason=Penalty.Reason.NO_SHOW,
        amount=5000,
    )
    apply_penalties_to_appointment(
        actor=context["owner"],
        appointment=expected,
        penalties=[expected_penalty],
    )
    expense = create_expense(
        actor=context["owner"],
        partner=context["partner"],
        date=date(2026, 1, 2),
        amount=10000,
        description="Materials",
    )

    report = finance_report_for_partner(
        partner=context["partner"],
        start_date=date(2026, 1, 2),
        end_date=date(2026, 1, 2),
        now=datetime(2026, 1, 2, 0, 0, tzinfo=datetime_timezone.utc),
    )

    assert report.actual_service_revenue_amount == 50000
    assert report.actual_penalty_revenue_amount == 5000
    assert report.actual_revenue_amount == 55000
    assert report.expected_service_revenue_amount == 50000
    assert report.expected_penalty_revenue_amount == 5000
    assert report.expected_revenue_amount == 55000
    assert report.expense_amount == expense.amount
    assert report.net_actual_amount == 45000
    assert Expense.objects.filter(id=expense.id).exists()
