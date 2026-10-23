from datetime import datetime, time, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.appointments.services import AppointmentServiceItemInput, create_confirmed_appointment
from apps.catalog.models import Service, ServiceCategory
from apps.clients.services import create_manual_client_profile
from apps.partners.services import create_partner_with_owner
from apps.schedule.models import BookingResource
from apps.schedule.services import create_availability_window, create_blocked_time
from core.timezones import get_zone


@pytest.mark.django_db
def test_partner_calendar_surfaces_only_selected_tenant_week(client) -> None:
    superuser = User.objects.create_superuser(username="root", password="secret")
    first = _create_calendar_context(
        superuser=superuser,
        partner_name="First Studio",
        username="first-calendar-owner",
        client_name="First Calendar Client",
        service_name="First Calendar Service",
    )
    second = _create_calendar_context(
        superuser=superuser,
        partner_name="Foreign Studio",
        username="foreign-calendar-owner",
        client_name="Foreign Calendar Client",
        service_name="Foreign Calendar Service",
    )
    week_start = _future_week_start(first["created"].partner.timezone)
    first_start = _local_datetime(first["created"].partner.timezone, week_start, 9)
    second_start = _local_datetime(second["created"].partner.timezone, week_start, 11)
    window = create_availability_window(
        actor=first["created"].owner,
        partner=first["created"].partner,
        booking_resource=first["resource"],
        start_at=first_start,
        end_at=first_start + timedelta(hours=8),
    )
    blocked = create_blocked_time(
        actor=first["created"].owner,
        partner=first["created"].partner,
        booking_resource=first["resource"],
        start_at=first_start + timedelta(hours=3),
        end_at=first_start + timedelta(hours=4),
        reason="Lunch break",
    )
    appointment = _create_appointment(first, start_at=first_start + timedelta(hours=1))
    _create_appointment(second, start_at=second_start)
    client.force_login(first["created"].owner)

    response = client.get(
        reverse("schedule:calendar"),
        {
            "partner": str(first["created"].partner.id),
            "week": week_start.isoformat(),
        },
    )

    assert response.status_code == 200
    snapshot = response.context["snapshot"]
    assert snapshot.partner == first["created"].partner
    assert snapshot.booking_resource == first["resource"]
    assert snapshot.week_start == week_start
    assert {entry.record for entry in snapshot.days[0].entries} == {
        window,
        blocked,
        appointment,
    }
    content = response.content.decode()
    assert "First Calendar Client" in content
    assert "First Calendar Service" in content
    assert "Lunch break" in content
    assert "Foreign Calendar Client" not in content
    assert "Foreign Calendar Service" not in content


@pytest.mark.django_db
def test_partner_calendar_rejects_foreign_partner_and_resource(client) -> None:
    superuser = User.objects.create_superuser(username="root", password="secret")
    first = _create_calendar_context(
        superuser=superuser,
        partner_name="First Studio",
        username="first-calendar-scope-owner",
        client_name="First Client",
        service_name="First Service",
    )
    second = _create_calendar_context(
        superuser=superuser,
        partner_name="Second Studio",
        username="second-calendar-scope-owner",
        client_name="Second Client",
        service_name="Second Service",
    )
    client.force_login(first["created"].owner)
    url = reverse("schedule:calendar")

    foreign_partner_response = client.get(
        url,
        {"partner": str(second["created"].partner.id)},
    )
    foreign_resource_response = client.get(
        url,
        {
            "partner": str(first["created"].partner.id),
            "resource": str(second["resource"].id),
        },
    )

    assert foreign_partner_response.status_code == 404
    assert foreign_resource_response.status_code == 404


@pytest.mark.django_db
def test_partner_calendar_uses_local_week_boundary(client) -> None:
    superuser = User.objects.create_superuser(username="root", password="secret")
    context = _create_calendar_context(
        superuser=superuser,
        partner_name="Kyiv Studio",
        username="kyiv-calendar-owner",
        client_name="Midnight Client",
        service_name="Midnight Service",
    )
    week_start = _future_week_start(context["created"].partner.timezone)
    local_start = datetime.combine(
        week_start,
        time(hour=0, minute=30),
        tzinfo=get_zone(context["created"].partner.timezone),
    )
    appointment = _create_appointment(context, start_at=local_start)
    client.force_login(context["created"].owner)

    response = client.get(
        reverse("schedule:calendar"),
        {"week": week_start.isoformat()},
    )

    assert response.status_code == 200
    snapshot = response.context["snapshot"]
    assert appointment.start_at.astimezone(get_zone("UTC")).date() < week_start
    assert [entry.record.id for entry in snapshot.days[0].entries] == [appointment.id]


@pytest.mark.django_db
def test_partner_calendar_keeps_booking_resources_separate(client) -> None:
    superuser = User.objects.create_superuser(username="root", password="secret")
    context = _create_calendar_context(
        superuser=superuser,
        partner_name="Resource Studio",
        username="resource-calendar-owner",
        client_name="Default Resource Client",
        service_name="Resource Service",
    )
    second_resource = BookingResource.objects.create(
        partner=context["created"].partner,
        name="Second chair",
        sort_order=1,
    )
    second_client = create_manual_client_profile(
        actor=context["created"].owner,
        partner=context["created"].partner,
        name="Second Resource Client",
    )
    week_start = _future_week_start(context["created"].partner.timezone)
    start_at = _local_datetime(context["created"].partner.timezone, week_start, 10)
    default_appointment = _create_appointment(context, start_at=start_at)
    second_appointment = _create_appointment(
        context,
        start_at=start_at,
        booking_resource=second_resource,
        client_profile=second_client,
    )
    client.force_login(context["created"].owner)

    response = client.get(
        reverse("schedule:calendar"),
        {
            "resource": str(second_resource.id),
            "week": week_start.isoformat(),
        },
    )

    assert response.status_code == 200
    snapshot = response.context["snapshot"]
    assert snapshot.booking_resource == second_resource
    assert [entry.record.id for entry in snapshot.days[0].entries] == [second_appointment.id]
    assert default_appointment.id not in {
        entry.record.id for day in snapshot.days for entry in day.entries
    }
    assert "Second Resource Client" in response.content.decode()
    assert "Default Resource Client" not in response.content.decode()


@pytest.mark.django_db
def test_calendar_manual_appointment_action_prefills_visible_context(client) -> None:
    superuser = User.objects.create_superuser(username="root", password="secret")
    context = _create_calendar_context(
        superuser=superuser,
        partner_name="Prefill Studio",
        username="prefill-calendar-owner",
        client_name="Prefill Client",
        service_name="Prefill Service",
    )
    client.force_login(context["created"].owner)

    response = client.get(
        reverse("admin:appointments_appointment_add"),
        {
            "partner": str(context["created"].partner.id),
            "booking_resource": str(context["resource"].id),
        },
    )

    assert response.status_code == 200
    assert response.context["form"].initial["partner"] == str(context["created"].partner.id)
    assert response.context["form"].initial["booking_resource"] == str(context["resource"].id)


def _create_calendar_context(
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


def _create_appointment(
    context,
    *,
    start_at,
    booking_resource=None,
    client_profile=None,
):
    return create_confirmed_appointment(
        actor=context["created"].owner,
        partner=context["created"].partner,
        booking_resource=booking_resource or context["resource"],
        client_profile=client_profile or context["client"],
        start_at=start_at,
        service_items=[AppointmentServiceItemInput(service=context["service"])],
    )


def _future_week_start(timezone_name: str):
    local_today = timezone.localdate(timezone=get_zone(timezone_name))
    return local_today - timedelta(days=local_today.weekday()) + timedelta(days=7)


def _local_datetime(timezone_name: str, day, hour: int):
    return datetime.combine(day, time(hour=hour), tzinfo=get_zone(timezone_name))
