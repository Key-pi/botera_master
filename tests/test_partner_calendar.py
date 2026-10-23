from datetime import datetime, time, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.appointments.models import Appointment
from apps.appointments.services import AppointmentServiceItemInput, create_confirmed_appointment
from apps.catalog.models import Service, ServiceCategory
from apps.clients.services import (
    create_manual_client_profile,
    link_client_profile_to_telegram_identity,
    upsert_telegram_identity,
)
from apps.partners.services import create_partner_with_owner
from apps.schedule.models import AvailabilityWindow, BlockedTime, BookingResource
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
    assert snapshot.view == "week"
    assert snapshot.period_start == week_start
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
def test_partner_calendar_switches_between_day_week_and_month(client) -> None:
    superuser = User.objects.create_superuser(username="root", password="secret")
    context = _create_calendar_context(
        superuser=superuser,
        partner_name="Calendar Views Studio",
        username="calendar-views-owner",
        client_name="Calendar Views Client",
        service_name="Calendar Views Service",
    )
    target_day = _future_week_start(context["created"].partner.timezone) + timedelta(days=2)
    appointment = _create_appointment(
        context,
        start_at=_local_datetime(context["created"].partner.timezone, target_day, 11),
    )
    client.force_login(context["created"].owner)
    url = reverse("schedule:calendar")
    base_query = {
        "partner": str(context["created"].partner.id),
        "resource": str(context["resource"].id),
        "date": target_day.isoformat(),
    }

    responses = {
        calendar_view: client.get(url, {**base_query, "view": calendar_view})
        for calendar_view in ("day", "week", "month")
    }

    assert all(response.status_code == 200 for response in responses.values())
    day_snapshot = responses["day"].context["snapshot"]
    assert day_snapshot.period_start == target_day
    assert day_snapshot.period_end == target_day
    assert len(day_snapshot.days) == 1

    week_snapshot = responses["week"].context["snapshot"]
    week_start = target_day - timedelta(days=target_day.weekday())
    assert week_snapshot.period_start == week_start
    assert week_snapshot.period_end == week_start + timedelta(days=6)
    assert len(week_snapshot.days) == 7

    month_snapshot = responses["month"].context["snapshot"]
    month_start = target_day.replace(day=1)
    next_month_probe = month_start.replace(day=28) + timedelta(days=4)
    next_month_start = next_month_probe.replace(day=1)
    assert month_snapshot.period_start == month_start
    assert month_snapshot.period_end == next_month_start - timedelta(days=1)
    assert len(month_snapshot.days) == month_snapshot.period_end.day
    assert len(month_snapshot.leading_blank_days) == month_start.weekday()

    for response in responses.values():
        snapshot = response.context["snapshot"]
        assert appointment.id in {
            entry.record.id for day in snapshot.days for entry in day.entries
        }
    assert 'aria-selected="true"' in responses["day"].content.decode()
    assert "Previous month" in responses["month"].content.decode()
    assert "Next month" in responses["month"].content.decode()


@pytest.mark.django_db
def test_partner_calendar_rejects_unknown_view(client) -> None:
    superuser = User.objects.create_superuser(username="root", password="secret")
    context = _create_calendar_context(
        superuser=superuser,
        partner_name="Unknown View Studio",
        username="unknown-view-owner",
        client_name="Unknown View Client",
        service_name="Unknown View Service",
    )
    client.force_login(context["created"].owner)

    response = client.get(reverse("schedule:calendar"), {"view": "agenda"})

    assert response.status_code == 404


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

    mismatched_detail_response = client.get(
        reverse("schedule:calendar"),
        {
            "partner": str(context["created"].partner.id),
            "resource": str(context["resource"].id),
            "week": week_start.isoformat(),
            "appointment": str(second_appointment.id),
        },
    )
    assert mismatched_detail_response.status_code == 404


@pytest.mark.django_db
def test_calendar_appointment_panel_reuses_tenant_safe_action_flows(client) -> None:
    superuser = User.objects.create_superuser(username="root", password="secret")
    context = _create_calendar_context(
        superuser=superuser,
        partner_name="Appointment Panel Studio",
        username="appointment-panel-owner",
        client_name="Appointment Panel Client",
        service_name="Appointment Panel Service",
    )
    identity = upsert_telegram_identity(
        telegram_user_id=907001,
        username="calendar-panel-client",
        first_name="Panel",
    )
    context["client"] = link_client_profile_to_telegram_identity(
        actor=context["created"].owner,
        client_profile=context["client"],
        telegram_identity=identity,
    )
    start_at = timezone.now() - timedelta(hours=2)
    appointment = create_confirmed_appointment(
        actor=context["created"].owner,
        partner=context["created"].partner,
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=start_at,
        service_items=[AppointmentServiceItemInput(service=context["service"])],
        client_comment="Detailed client comment",
        private_master_note="Private appointment note",
    )
    local_day = start_at.astimezone(get_zone(context["created"].partner.timezone)).date()
    week_start = local_day - timedelta(days=local_day.weekday())
    client.force_login(context["created"].owner)

    response = client.get(
        reverse("schedule:calendar"),
        {
            "partner": str(context["created"].partner.id),
            "resource": str(context["resource"].id),
            "week": week_start.isoformat(),
            "appointment": str(appointment.id),
        },
    )

    assert response.status_code == 200
    assert response.context["selected_appointment"] == appointment
    actions = response.context["appointment_actions"]
    assert actions["record_url"] == reverse(
        "admin:appointments_appointment_change",
        args=[appointment.id],
    )
    assert actions["client_url"] == reverse(
        "admin:clients_clientprofile_change",
        args=[context["client"].id],
    )
    assert actions["complete_url"].endswith("?target=completed")
    assert actions["no_show_url"].endswith("?target=no_show")
    assert actions["cancel_url"].endswith("?target=cancelled_by_master")
    assert actions["proposal_url"] == reverse(
        "admin:appointments_appointment_propose_time",
        args=[appointment.id],
    )
    assert actions["message_url"] == reverse(
        "admin:messaging_conversationmessage_reply",
        args=[context["client"].id],
    )
    content = response.content.decode()
    assert "Detailed client comment" in content
    assert "Private appointment note" in content
    assert "Appointment Panel Service" in content
    assert "500.00 UAH" in content
    assert "Mark completed" in content
    assert "Mark no-show" in content
    assert "Propose another time" in content
    assert "Message client" in content

    preselected_status_response = client.get(actions["complete_url"])
    assert preselected_status_response.status_code == 200
    assert preselected_status_response.context["form"]["new_status"].value() == (
        Appointment.Status.COMPLETED
    )


@pytest.mark.django_db
def test_calendar_appointment_panel_hides_unavailable_future_and_telegram_actions(client) -> None:
    superuser = User.objects.create_superuser(username="root", password="secret")
    context = _create_calendar_context(
        superuser=superuser,
        partner_name="Future Panel Studio",
        username="future-panel-owner",
        client_name="Manual Future Client",
        service_name="Future Panel Service",
    )
    week_start = _future_week_start(context["created"].partner.timezone)
    appointment = _create_appointment(
        context,
        start_at=_local_datetime(context["created"].partner.timezone, week_start, 10),
    )
    client.force_login(context["created"].owner)

    response = client.get(
        reverse("schedule:calendar"),
        {
            "partner": str(context["created"].partner.id),
            "resource": str(context["resource"].id),
            "week": week_start.isoformat(),
            "appointment": str(appointment.id),
        },
    )

    assert response.status_code == 200
    actions = response.context["appointment_actions"]
    assert actions["complete_url"] is None
    assert actions["no_show_url"] is None
    assert actions["proposal_url"] is None
    assert actions["message_url"] is None
    assert actions["cancel_url"] is not None
    content = response.content.decode()
    assert "Mark completed" not in content
    assert "Mark no-show" not in content
    assert "This client has no Telegram connection" in content


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


@pytest.mark.django_db
def test_calendar_quick_add_creates_availability_and_blocked_time_in_partner_timezone(
    client,
) -> None:
    superuser = User.objects.create_superuser(username="root", password="secret")
    context = _create_calendar_context(
        superuser=superuser,
        partner_name="Quick Schedule Studio",
        username="quick-schedule-owner",
        client_name="Quick Client",
        service_name="Quick Service",
    )
    week_start = _future_week_start(context["created"].partner.timezone)
    target_day = week_start + timedelta(days=2)
    client.force_login(context["created"].owner)
    url = _quick_create_url(context=context, week_start=week_start, target_day=target_day)

    open_response = client.get(url)
    availability_response = client.post(
        url,
        {
            "form_action": "quick_create",
            "item_type": "availability",
            "day": target_day.isoformat(),
            "start_time": "18:00",
            "end_time": "21:00",
            "reason": "",
        },
    )
    blocked_response = client.post(
        url,
        {
            "form_action": "quick_create",
            "item_type": "blocked",
            "day": target_day.isoformat(),
            "start_time": "19:00",
            "end_time": "19:30",
            "reason": "Personal break",
        },
    )

    assert open_response.status_code == 200
    assert f'value="{target_day.isoformat()}"' in open_response.content.decode()
    assert availability_response.status_code == 302
    assert blocked_response.status_code == 302
    window = AvailabilityWindow.objects.get(partner=context["created"].partner)
    blocked = BlockedTime.objects.get(partner=context["created"].partner)
    partner_zone = get_zone(context["created"].partner.timezone)
    assert window.booking_resource == context["resource"]
    assert window.start_at.astimezone(partner_zone).replace(tzinfo=None) == datetime.combine(
        target_day,
        time(hour=18),
    )
    assert window.end_at.astimezone(partner_zone).replace(tzinfo=None) == datetime.combine(
        target_day,
        time(hour=21),
    )
    assert blocked.booking_resource == context["resource"]
    assert blocked.reason == "Personal break"
    assert blocked.created_by == context["created"].owner


@pytest.mark.django_db
def test_calendar_quick_add_hands_appointment_time_to_manual_flow(client) -> None:
    superuser = User.objects.create_superuser(username="root", password="secret")
    context = _create_calendar_context(
        superuser=superuser,
        partner_name="Quick Appointment Studio",
        username="quick-appointment-owner",
        client_name="Appointment Client",
        service_name="Appointment Service",
    )
    week_start = _future_week_start(context["created"].partner.timezone)
    target_day = week_start + timedelta(days=3)
    client.force_login(context["created"].owner)

    response = client.post(
        _quick_create_url(context=context, week_start=week_start, target_day=target_day),
        {
            "form_action": "quick_create",
            "item_type": "appointment",
            "day": target_day.isoformat(),
            "start_time": "14:30",
            "end_time": "",
            "reason": "",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].startswith(reverse("admin:appointments_appointment_add"))
    manual_response = client.get(response.headers["Location"])
    assert manual_response.status_code == 200
    assert manual_response.context["form"].initial["partner"] == str(context["created"].partner.id)
    assert manual_response.context["form"].initial["booking_resource"] == str(
        context["resource"].id
    )
    assert manual_response.context["form"].initial["start_at"] == datetime.combine(
        target_day,
        time(hour=14, minute=30),
    )
    assert Appointment.objects.filter(partner=context["created"].partner).exists() is False


@pytest.mark.django_db
def test_calendar_copy_day_adds_schedule_without_copying_appointments(client) -> None:
    superuser = User.objects.create_superuser(username="root", password="secret")
    context = _create_calendar_context(
        superuser=superuser,
        partner_name="Copy Day Studio",
        username="copy-day-owner",
        client_name="Copy Day Client",
        service_name="Copy Day Service",
    )
    week_start = _future_week_start(context["created"].partner.timezone)
    source_day = week_start + timedelta(days=1)
    target_day = source_day + timedelta(days=2)
    source_start = _local_datetime(context["created"].partner.timezone, source_day, 10)
    create_availability_window(
        actor=context["created"].owner,
        partner=context["created"].partner,
        booking_resource=context["resource"],
        start_at=source_start,
        end_at=source_start + timedelta(hours=4),
    )
    create_blocked_time(
        actor=context["created"].owner,
        partner=context["created"].partner,
        booking_resource=context["resource"],
        start_at=source_start + timedelta(hours=1),
        end_at=source_start + timedelta(hours=1, minutes=30),
        reason="Lunch",
    )
    _create_appointment(context, start_at=source_start + timedelta(hours=2))
    client.force_login(context["created"].owner)
    url = _copy_schedule_url(
        context=context,
        week_start=week_start,
        source_date=source_day,
        scope="day",
    )

    open_response = client.get(url)
    copy_response = client.post(
        url,
        {
            "form_action": "copy_schedule",
            "target_date": target_day.isoformat(),
        },
    )

    assert open_response.status_code == 200
    assert f'value="{(source_day + timedelta(days=1)).isoformat()}"' in (
        open_response.content.decode()
    )
    assert b"appointments are never copied" in open_response.content
    assert copy_response.status_code == 302
    partner_zone = get_zone(context["created"].partner.timezone)
    copied_window = AvailabilityWindow.objects.exclude(start_at=source_start).get(
        partner=context["created"].partner
    )
    copied_block = BlockedTime.objects.exclude(start_at=source_start + timedelta(hours=1)).get(
        partner=context["created"].partner
    )
    assert copied_window.start_at.astimezone(partner_zone).date() == target_day
    assert copied_window.start_at.astimezone(partner_zone).time() == time(10)
    assert copied_block.reason == "Lunch"
    assert Appointment.objects.filter(partner=context["created"].partner).count() == 1


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


def _quick_create_url(*, context, week_start, target_day) -> str:
    return (
        f"{reverse('schedule:calendar')}"
        f"?partner={context['created'].partner.id}"
        f"&resource={context['resource'].id}"
        f"&week={week_start.isoformat()}"
        f"&create=1&date={target_day.isoformat()}"
    )


def _copy_schedule_url(*, context, week_start, source_date, scope: str) -> str:
    return (
        f"{reverse('schedule:calendar')}"
        f"?partner={context['created'].partner.id}"
        f"&resource={context['resource'].id}"
        f"&week={week_start.isoformat()}"
        f"&copy={scope}&source={source_date.isoformat()}"
    )
