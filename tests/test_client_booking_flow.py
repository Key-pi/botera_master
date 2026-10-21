from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.appointments.client_booking import (
    available_booking_dates,
    available_booking_slots,
    available_client_reschedule_slots,
    booking_category_page,
    booking_services_page,
    build_booking_summary,
    cancel_client_requested_booking,
    client_appointment_history_page,
    client_profile_overview,
    confirm_client_booking,
    reschedule_client_appointment,
)
from apps.appointments.models import Appointment, AppointmentProposal
from apps.appointments.services import (
    AppointmentServiceItemInput,
    cancel_appointment_by_client,
    client_cancellation_policy,
    create_confirmed_appointment,
    transition_appointment_status,
)
from apps.bot.pagination import MAX_TELEGRAM_ENTITY_PAGE_SIZE, paginate_telegram_entities
from apps.catalog.services import create_service, create_service_category
from apps.clients.services import create_manual_client_profile, update_client_profile_language
from apps.finance.models import Penalty
from apps.finance.services import update_penalty_settings
from apps.notifications.models import ScheduledNotification
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
def test_client_auto_reschedule_moves_same_appointment_and_notifies_master(
    booking_context,
    rounded_future_start,
    django_capture_on_commit_callbacks,
):
    context = booking_context
    update_schedule_settings(
        actor=context["owner"],
        partner=context["partner"],
        confirmation_mode=PartnerScheduleSettings.ConfirmationMode.AUTO,
        slot_step_minutes=30,
    )
    _open_window(context, rounded_future_start, hours=3)
    appointment = create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=rounded_future_start,
        service_items=[AppointmentServiceItemInput(service=context["service"])],
    )
    new_start = rounded_future_start + timedelta(hours=1)

    with django_capture_on_commit_callbacks(execute=True):
        result = reschedule_client_appointment(
            partner=context["partner"],
            client_profile=context["client"],
            appointment=appointment,
            start_at=new_start,
            now=rounded_future_start - timedelta(hours=1),
        )

    appointment.refresh_from_db()
    assert result.requires_master_confirmation is False
    assert result.proposal is None
    assert appointment.start_at == new_start
    assert appointment.end_at == new_start + timedelta(hours=1)
    assert appointment.status == Appointment.Status.CONFIRMED
    assert appointment.service_items.get().service_name_snapshot == "Manicure"
    assert ScheduledNotification.objects.filter(
        appointment=appointment,
        type=ScheduledNotification.Type.MASTER_CLIENT_REQUESTED_RESCHEDULE,
    ).exists()


@pytest.mark.django_db
def test_client_manual_reschedule_keeps_old_time_and_creates_partner_proposal(
    booking_context,
    rounded_future_start,
    django_capture_on_commit_callbacks,
):
    context = booking_context
    update_schedule_settings(
        actor=context["owner"],
        partner=context["partner"],
        confirmation_mode=PartnerScheduleSettings.ConfirmationMode.MANUAL,
        slot_step_minutes=30,
    )
    _open_window(context, rounded_future_start, hours=3)
    appointment = create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=rounded_future_start,
        service_items=[AppointmentServiceItemInput(service=context["service"])],
    )
    new_start = rounded_future_start + timedelta(hours=1)

    with django_capture_on_commit_callbacks(execute=True):
        result = reschedule_client_appointment(
            partner=context["partner"],
            client_profile=context["client"],
            appointment=appointment,
            start_at=new_start,
            now=rounded_future_start - timedelta(hours=1),
        )

    appointment.refresh_from_db()
    proposal = AppointmentProposal.objects.get(appointment=appointment)
    option = proposal.options.get()
    assert result.requires_master_confirmation is True
    assert result.proposal == proposal
    assert appointment.start_at == rounded_future_start
    assert proposal.created_by is None
    assert proposal.status == AppointmentProposal.Status.PENDING
    assert option.start_at == new_start
    assert option.is_near_fit is False
    assert ScheduledNotification.objects.filter(
        appointment_proposal=proposal,
        type=ScheduledNotification.Type.MASTER_CLIENT_REQUESTED_RESCHEDULE,
    ).exists()
    assert not ScheduledNotification.objects.filter(
        appointment_proposal=proposal,
        type=ScheduledNotification.Type.CLIENT_APPOINTMENT_PROPOSAL,
    ).exists()


@pytest.mark.django_db
def test_near_fit_client_reschedule_requires_master_even_in_auto_mode(
    booking_context,
    rounded_future_start,
):
    context = booking_context
    update_schedule_settings(
        actor=context["owner"],
        partner=context["partner"],
        confirmation_mode=PartnerScheduleSettings.ConfirmationMode.AUTO,
        slot_step_minutes=30,
        near_fit_tolerance_minutes=10,
    )
    appointment = create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=rounded_future_start,
        service_items=[AppointmentServiceItemInput(service=context["service"])],
    )
    new_start = rounded_future_start + timedelta(days=1)
    create_availability_window(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        start_at=new_start,
        end_at=new_start + timedelta(minutes=50),
    )

    result = reschedule_client_appointment(
        partner=context["partner"],
        client_profile=context["client"],
        appointment=appointment,
        start_at=new_start,
        now=rounded_future_start - timedelta(hours=1),
    )

    appointment.refresh_from_db()
    assert result.requires_master_confirmation is True
    assert result.is_near_fit is True
    assert appointment.start_at == rounded_future_start
    assert result.proposal.options.get().is_near_fit is True


@pytest.mark.django_db
def test_reschedule_slots_exclude_current_appointment_but_not_other_bookings(
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
    _open_window(context, rounded_future_start, hours=3)
    appointment = create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=rounded_future_start,
        service_items=[AppointmentServiceItemInput(service=context["service"])],
    )

    slots = available_client_reschedule_slots(
        partner=context["partner"],
        client_profile=context["client"],
        appointment=appointment,
        local_date=rounded_future_start.date(),
        now=rounded_future_start - timedelta(hours=1),
    )

    starts = {slot.start_at for slot in slots.items}
    assert rounded_future_start not in starts
    assert rounded_future_start + timedelta(hours=1) in starts


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
def test_client_can_cancel_confirmed_booking_before_deadline_without_penalty(
    booking_context,
    rounded_future_start,
    django_capture_on_commit_callbacks,
):
    context = booking_context
    update_penalty_settings(
        actor=context["owner"],
        partner=context["partner"],
        penalties_enabled=True,
    )
    appointment = create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=rounded_future_start,
        service_items=[AppointmentServiceItemInput(service=context["service"])],
    )

    with django_capture_on_commit_callbacks(execute=True):
        result = cancel_appointment_by_client(
            client_profile=context["client"],
            appointment=appointment,
            now=rounded_future_start - timedelta(hours=7),
        )

    assert result.appointment.status == Appointment.Status.CANCELLED_BY_CLIENT
    assert result.penalty is None
    assert Penalty.objects.filter(source_appointment=appointment).exists() is False
    assert (
        ScheduledNotification.objects.filter(
            appointment=appointment,
            type=ScheduledNotification.Type.MASTER_CLIENT_CANCELLED_APPOINTMENT,
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_late_client_cancellation_requires_acknowledged_penalty(
    booking_context,
    rounded_future_start,
):
    context = booking_context
    update_penalty_settings(
        actor=context["owner"],
        partner=context["partner"],
        penalties_enabled=True,
        late_cancel_penalty_amount=5000,
    )
    appointment = create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=rounded_future_start,
        service_items=[AppointmentServiceItemInput(service=context["service"])],
    )
    cancellation_time = rounded_future_start - timedelta(hours=2)

    policy = client_cancellation_policy(
        client_profile=context["client"],
        appointment=appointment,
        now=cancellation_time,
    )
    with pytest.raises(ValidationError, match="Confirm the late-cancellation penalty"):
        cancel_appointment_by_client(
            client_profile=context["client"],
            appointment=appointment,
            now=cancellation_time,
        )
    result = cancel_appointment_by_client(
        client_profile=context["client"],
        appointment=appointment,
        accept_late_penalty=True,
        now=cancellation_time,
    )

    assert policy.allowed is True
    assert policy.requires_penalty_confirmation is True
    assert policy.penalty_amount == 5000
    assert result.appointment.status == Appointment.Status.CANCELLED_BY_CLIENT
    assert result.penalty is not None
    assert result.penalty.reason == Penalty.Reason.LATE_CANCEL
    assert result.penalty.amount == 5000
    with pytest.raises(ValidationError, match="can no longer be cancelled"):
        cancel_appointment_by_client(
            client_profile=context["client"],
            appointment=appointment,
            accept_late_penalty=True,
            now=cancellation_time,
        )
    assert Penalty.objects.filter(source_appointment=appointment).count() == 1


@pytest.mark.django_db
def test_late_client_cancellation_is_blocked_when_penalties_are_disabled(
    booking_context,
    rounded_future_start,
):
    context = booking_context
    appointment = create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=rounded_future_start,
        service_items=[AppointmentServiceItemInput(service=context["service"])],
    )
    cancellation_time = rounded_future_start - timedelta(hours=2)

    policy = client_cancellation_policy(
        client_profile=context["client"],
        appointment=appointment,
        now=cancellation_time,
    )

    assert policy.allowed is False
    assert policy.reason_code == "late_cancellation_blocked"
    with pytest.raises(ValidationError, match="deadline has passed"):
        cancel_appointment_by_client(
            client_profile=context["client"],
            appointment=appointment,
            now=cancellation_time,
        )
    appointment.refresh_from_db()
    assert appointment.status == Appointment.Status.CONFIRMED


@pytest.mark.django_db
def test_client_cancellation_rejects_another_clients_appointment(
    booking_context,
    rounded_future_start,
):
    context = booking_context
    other_client = create_manual_client_profile(
        actor=context["owner"],
        partner=context["partner"],
        name="Other client",
    )
    appointment = create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["client"],
        start_at=rounded_future_start,
        service_items=[AppointmentServiceItemInput(service=context["service"])],
    )

    with pytest.raises(ValidationError, match="does not belong to this client"):
        cancel_appointment_by_client(
            client_profile=other_client,
            appointment=appointment,
            now=rounded_future_start - timedelta(hours=7),
        )


@pytest.mark.django_db
def test_client_history_and_profile_overview_are_paginated_newest_first(
    booking_context,
    rounded_future_start,
):
    context = booking_context
    appointments = []
    for index in range(6):
        if index < 2:
            start_at = timezone.now() - timedelta(days=2 - index)
        else:
            start_at = rounded_future_start + timedelta(days=index - 2)
        appointment = create_confirmed_appointment(
            actor=context["owner"],
            partner=context["partner"],
            booking_resource=context["resource"],
            client_profile=context["client"],
            start_at=start_at,
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
