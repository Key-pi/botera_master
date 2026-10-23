from datetime import date, datetime, timedelta
from datetime import timezone as datetime_timezone

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.appointments.services import (
    AppointmentServiceItemInput,
    create_confirmed_appointment,
    create_requested_appointment,
)
from apps.catalog.models import Service, ServiceCategory
from apps.clients.services import create_manual_client_profile
from apps.notifications.models import ScheduledNotification
from apps.partners.selectors import partner_dashboard_snapshot
from apps.partners.services import create_partner_with_owner
from apps.schedule.models import BookingResource


@pytest.mark.django_db
def test_partner_dashboard_surfaces_only_tenant_operational_data(client) -> None:
    superuser = User.objects.create_superuser(username="root", password="secret")
    first = _create_dashboard_context(
        superuser=superuser,
        partner_name="First Studio",
        username="first-dashboard-owner",
        client_name="First Client",
        service_name="First Service",
    )
    second = _create_dashboard_context(
        superuser=superuser,
        partner_name="Second Studio",
        username="second-dashboard-owner",
        client_name="Foreign Client",
        service_name="Foreign Service",
    )
    first_request = _create_request(first, start_at=timezone.now() + timedelta(hours=2))
    second_request = _create_request(second, start_at=timezone.now() + timedelta(hours=3))
    _create_failed_notification(
        context=first,
        appointment=first_request,
        key="first-dashboard-failure",
        failure_code="bot_blocked",
    )
    _create_failed_notification(
        context=second,
        appointment=second_request,
        key="foreign-dashboard-failure",
        failure_code="foreign_failure",
    )
    client.force_login(first["created"].owner)

    response = client.get(reverse("partners:dashboard"))

    assert response.status_code == 200
    snapshot = response.context["snapshot"]
    assert snapshot.partner == first["created"].partner
    assert snapshot.pending_request_count == 1
    assert snapshot.failed_notification_count == 1
    content = response.content.decode()
    assert "First Studio" in content
    assert "First Client" in content
    assert "First Service" in content
    assert "bot_blocked" in content
    assert "Foreign Client" not in content
    assert "Foreign Service" not in content
    assert "foreign_failure" not in content


@pytest.mark.django_db
def test_partner_dashboard_rejects_foreign_partner_selection(client) -> None:
    superuser = User.objects.create_superuser(username="root", password="secret")
    first = create_partner_with_owner(
        actor=superuser,
        partner_name="First",
        owner_username="first-selection-owner",
        owner_password="secret",
    )
    second = create_partner_with_owner(
        actor=superuser,
        partner_name="Second",
        owner_username="second-selection-owner",
        owner_password="secret",
    )
    client.force_login(first.owner)

    response = client.get(
        reverse("partners:dashboard"),
        {"partner": str(second.partner.id)},
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_partner_dashboard_uses_partner_timezone_for_day_boundaries() -> None:
    superuser = User.objects.create_superuser(username="root", password="secret")
    context = _create_dashboard_context(
        superuser=superuser,
        partner_name="Kyiv Studio",
        username="kyiv-dashboard-owner",
        client_name="Timezone Client",
        service_name="Timezone Service",
    )
    fixed_now = datetime(2026, 10, 22, 21, 30, tzinfo=datetime_timezone.utc)
    previous_local_day = _create_request(
        context,
        start_at=datetime(2026, 10, 22, 20, 0, tzinfo=datetime_timezone.utc),
    )
    local_today = create_confirmed_appointment(
        actor=context["created"].owner,
        partner=context["created"].partner,
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=datetime(2026, 10, 22, 22, 0, tzinfo=datetime_timezone.utc),
        service_items=[AppointmentServiceItemInput(service=context["service"])],
    )

    snapshot = partner_dashboard_snapshot(
        partner=context["created"].partner,
        now=fixed_now,
    )

    assert snapshot.today == date(2026, 10, 23)
    assert [appointment.id for appointment in snapshot.today_appointments] == [local_today.id]
    assert previous_local_day.id not in {
        appointment.id for appointment in snapshot.today_appointments
    }
    assert snapshot.today_finance.expected_revenue_amount == 50_000


@pytest.mark.django_db
def test_root_redirects_to_partner_dashboard(client) -> None:
    superuser = User.objects.create_superuser(username="root", password="secret")
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Root Partner",
        owner_username="root-redirect-owner",
        owner_password="secret",
    )
    client.force_login(created.owner)

    response = client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("partners:dashboard")


def _create_dashboard_context(
    *,
    superuser,
    partner_name: str,
    username: str,
    client_name: str,
    service_name: str,
):
    created = create_partner_with_owner(
        actor=superuser,
        partner_name=partner_name,
        owner_username=username,
        owner_password="secret",
    )
    category = ServiceCategory.objects.create(
        partner=created.partner,
        name=f"{service_name} category",
    )
    service = Service.objects.create(
        partner=created.partner,
        category=category,
        name=service_name,
        price_amount=50_000,
        duration_minutes=60,
    )
    client_profile = create_manual_client_profile(
        actor=created.owner,
        partner=created.partner,
        name=client_name,
    )
    return {
        "created": created,
        "resource": BookingResource.objects.get(partner=created.partner, is_default=True),
        "client": client_profile,
        "service": service,
    }


def _create_request(context, *, start_at):
    return create_requested_appointment(
        actor=context["created"].owner,
        partner=context["created"].partner,
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=start_at,
        service_items=[AppointmentServiceItemInput(service=context["service"])],
    )


def _create_failed_notification(*, context, appointment, key: str, failure_code: str):
    return ScheduledNotification.objects.create(
        partner=context["created"].partner,
        appointment=appointment,
        client_profile=context["client"],
        type=ScheduledNotification.Type.CLIENT_APPOINTMENT_REMINDER,
        due_at=timezone.now(),
        status=ScheduledNotification.Status.FAILED,
        idempotency_key=key,
        attempt_count=3,
        failed_visible_to_partner=True,
        last_failure_code=failure_code,
    )
