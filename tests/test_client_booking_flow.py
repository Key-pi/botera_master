from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.appointments.client_booking import (
    available_booking_dates,
    available_booking_slots,
    booking_category_page,
    booking_services_page,
    build_booking_summary,
    cancel_client_requested_booking,
    client_appointment_history_page,
    client_profile_overview,
    confirm_client_booking,
)
from apps.appointments.models import Appointment
from apps.appointments.services import (
    AppointmentServiceItemInput,
    create_confirmed_appointment,
    transition_appointment_status,
)
from apps.bot.pagination import MAX_TELEGRAM_ENTITY_PAGE_SIZE, paginate_telegram_entities
from apps.catalog.services import create_service, create_service_category
from apps.clients.services import create_manual_client_profile, update_client_profile_language
from apps.partners.services import create_partner_with_owner
from apps.schedule.models import PartnerScheduleSettings
from apps.schedule.selectors import default_booking_resource_for_partner
from apps.schedule.services import (
    create_availability_window,
    update_schedule_settings,
)


@pytest.fixture
def booking_context():
    superuser = User.objects.create_superuser(username="root", password="secret")
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Booking Partner",
        owner_username="booking-owner",
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
        "category": category,
        "service": service,
        "client": client,
    }


@pytest.fixture
def rounded_future_start():
    return (timezone.now() + timedelta(days=4)).replace(
        hour=9,
        minute=0,
        second=0,
        microsecond=0,
    )


def _open_window(context, start_at, *, hours=2):
    create_availability_window(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        start_at=start_at,
        end_at=start_at + timedelta(hours=hours),
    )


@pytest.mark.django_db
def test_client_can_browse_categories_services_summary_dates_and_slots(
    booking_context,
    rounded_future_start,
):
    context = booking_context
    update_schedule_settings(
        actor=context["owner"],
        partner=context["partner"],
        confirmation_mode=PartnerScheduleSettings.ConfirmationMode.AUTO,
        slot_step_minutes=30,
    )
    _open_window(context, rounded_future_start)

    categories = booking_category_page(partner=context["partner"])
    services = booking_services_page(
        partner=context["partner"],
        category=context["category"],
    )
    summary = build_booking_summary(
        partner=context["partner"],
        selected_service_ids=[context["service"].id],
    )
    dates = available_booking_dates(
        partner=context["partner"],
        selected_service_ids=[context["service"].id],
        start_date=rounded_future_start.date(),
        days=7,
        now=rounded_future_start - timedelta(hours=1),
    )
    slots = available_booking_slots(
        partner=context["partner"],
        selected_service_ids=[context["service"].id],
        local_date=rounded_future_start.date(),
        now=rounded_future_start - timedelta(hours=1),
    )

    assert categories.items == (context["category"],)
    assert services.items == (context["service"],)
    assert summary.duration_minutes == 60
    assert summary.service_total_amount == 50000
    assert dates.items[0].local_date == rounded_future_start.date()
    assert slots.items[0].start_at == rounded_future_start
    assert slots.items[0].is_near_fit is False


@pytest.mark.django_db
def test_auto_confirm_booking_creates_confirmed_and_blocks_same_slot(
    booking_context,
    rounded_future_start,
):
    context = booking_context
    update_schedule_settings(
        actor=context["owner"],
        partner=context["partner"],
        confirmation_mode=PartnerScheduleSettings.ConfirmationMode.AUTO,
    )
    _open_window(context, rounded_future_start)

    result = confirm_client_booking(
        partner=context["partner"],
        client_profile=context["client"],
        selected_service_ids=[context["service"].id],
        start_at=rounded_future_start,
        now=rounded_future_start - timedelta(hours=1),
    )

    assert result.appointment.status == Appointment.Status.CONFIRMED
    assert result.requires_master_confirmation is False

    with pytest.raises(ValidationError):
        confirm_client_booking(
            partner=context["partner"],
            client_profile=context["client"],
            selected_service_ids=[context["service"].id],
            start_at=rounded_future_start,
            now=rounded_future_start - timedelta(hours=1),
        )


@pytest.mark.django_db
def test_manual_confirm_booking_creates_requested_appointment(
    booking_context,
    rounded_future_start,
):
    context = booking_context
    update_schedule_settings(
        actor=context["owner"],
        partner=context["partner"],
        confirmation_mode=PartnerScheduleSettings.ConfirmationMode.MANUAL,
    )
    _open_window(context, rounded_future_start)

    result = confirm_client_booking(
        partner=context["partner"],
        client_profile=context["client"],
        selected_service_ids=[context["service"].id],
        start_at=rounded_future_start,
        now=rounded_future_start - timedelta(hours=1),
    )

    assert result.appointment.status == Appointment.Status.REQUESTED
    assert result.requires_master_confirmation is True
    assert result.appointment.confirmation_mode_snapshot == "manual"


@pytest.mark.django_db
def test_near_fit_booking_is_requested_even_when_auto_confirm(
    booking_context,
    rounded_future_start,
):
    context = booking_context
    update_schedule_settings(
        actor=context["owner"],
        partner=context["partner"],
        confirmation_mode=PartnerScheduleSettings.ConfirmationMode.AUTO,
        near_fit_tolerance_minutes=10,
    )
    long_service = create_service(
        actor=context["owner"],
        partner=context["partner"],
        category=context["category"],
        name="Long manicure",
        price_amount=70000,
        duration_minutes=70,
    )
    create_availability_window(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        start_at=rounded_future_start,
        end_at=rounded_future_start + timedelta(minutes=60),
    )

    result = confirm_client_booking(
        partner=context["partner"],
        client_profile=context["client"],
        selected_service_ids=[long_service.id],
        start_at=rounded_future_start,
        now=rounded_future_start - timedelta(hours=1),
    )

    assert result.appointment.status == Appointment.Status.REQUESTED
    assert result.appointment.is_near_fit is True
    assert result.is_near_fit is True


@pytest.mark.django_db
def test_final_confirmation_revalidates_inactive_service(
    booking_context,
    rounded_future_start,
):
    context = booking_context
    update_schedule_settings(
        actor=context["owner"],
        partner=context["partner"],
        confirmation_mode=PartnerScheduleSettings.ConfirmationMode.AUTO,
    )
    _open_window(context, rounded_future_start)
    context["service"].is_active = False
    context["service"].save(update_fields=["is_active", "updated_at"])

    with pytest.raises(ValidationError):
        confirm_client_booking(
            partner=context["partner"],
            client_profile=context["client"],
            selected_service_ids=[context["service"].id],
            start_at=rounded_future_start,
            now=rounded_future_start - timedelta(hours=1),
        )


@pytest.mark.django_db
def test_client_can_cancel_requested_booking(booking_context, rounded_future_start):
    context = booking_context
    update_schedule_settings(
        actor=context["owner"],
        partner=context["partner"],
        confirmation_mode=PartnerScheduleSettings.ConfirmationMode.MANUAL,
    )
    _open_window(context, rounded_future_start)
    result = confirm_client_booking(
        partner=context["partner"],
        client_profile=context["client"],
        selected_service_ids=[context["service"].id],
        start_at=rounded_future_start,
        now=rounded_future_start - timedelta(hours=1),
    )

    cancelled = cancel_client_requested_booking(
        client_profile=context["client"],
        appointment=result.appointment,
        reason="Changed plans",
    )

    assert cancelled.status == Appointment.Status.CANCELLED_BY_CLIENT
    assert cancelled.client_visible_reason == "Changed plans"


@pytest.mark.django_db
def test_client_history_and_profile_overview_are_paginated_newest_first(
    booking_context,
    rounded_future_start,
):
    context = booking_context
    appointments = []
    for index in range(6):
        appointment = create_confirmed_appointment(
            actor=context["owner"],
            partner=context["partner"],
            booking_resource=context["resource"],
            client_profile=context["client"],
            start_at=rounded_future_start + timedelta(days=index),
            service_items=[AppointmentServiceItemInput(service=context["service"])],
        )
        if index < 2:
            transition_appointment_status(
                actor=context["owner"],
                appointment=appointment,
                new_status=Appointment.Status.COMPLETED,
            )
        appointments.append(appointment)

    history = client_appointment_history_page(client_profile=context["client"])
    overview = client_profile_overview(
        client_profile=context["client"],
        now=rounded_future_start - timedelta(hours=1),
    )

    assert history.per_page == 5
    assert history.total_items == 6
    assert history.items[0].id == appointments[-1].id
    assert overview.visit_count == 2
    assert overview.future_appointments.total_items == 4


@pytest.mark.django_db
def test_client_language_can_be_changed_from_profile(booking_context):
    profile = update_client_profile_language(
        client_profile=booking_context["client"],
        language="en",
    )

    assert profile.language == "en"

    with pytest.raises(ValidationError):
        update_client_profile_language(client_profile=profile, language="de")


def test_telegram_entity_pagination_caps_large_page_sizes():
    page = paginate_telegram_entities(tuple(range(30)), page=1, per_page=99)

    assert page.per_page == MAX_TELEGRAM_ENTITY_PAGE_SIZE
    assert len(page.items) == MAX_TELEGRAM_ENTITY_PAGE_SIZE
