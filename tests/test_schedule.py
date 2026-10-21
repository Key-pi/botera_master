from datetime import time, timedelta

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from apps.accounts.models import PartnerMembership, User
from apps.catalog.services import create_service, create_service_category
from apps.partners.services import create_partner_with_owner
from apps.partners.setup import SetupStep, get_partner_setup_status
from apps.schedule.selectors import default_booking_resource_for_partner
from apps.schedule.services import (
    TimeInterval,
    apply_availability_template,
    create_availability_template,
    create_availability_template_window,
    create_availability_window,
    create_blocked_time,
    duration_minutes_for_services,
    generate_available_slots,
    update_availability_template_window,
    update_schedule_settings,
)


@pytest.fixture
def partner_with_schedule():
    superuser = User.objects.create_superuser(username="root", password="secret")
    return create_partner_with_owner(
        actor=superuser,
        partner_name="Schedule Partner",
        owner_username="schedule-owner",
        owner_password="secret",
    )


@pytest.fixture
def rounded_future_start():
    now = timezone.now()
    return (now + timedelta(days=2)).replace(minute=0, second=0, microsecond=0)


@pytest.mark.django_db
def test_partner_creation_initializes_default_resource_and_schedule_settings(partner_with_schedule):
    created = partner_with_schedule
    booking_resource = default_booking_resource_for_partner(created.partner)
    setup_status = get_partner_setup_status(created.partner)

    assert booking_resource is not None
    assert booking_resource.is_default is True
    assert booking_resource.is_active is True
    assert booking_resource.advisory_lock_key > 0
    assert hasattr(created.partner, "schedule_settings")
    assert SetupStep.BOOKING_CONFIRMATION_MODE in setup_status.missing_steps
    assert SetupStep.AVAILABILITY_WINDOW in setup_status.missing_steps


@pytest.mark.django_db
def test_schedule_settings_and_availability_complete_setup_steps(
    partner_with_schedule,
    rounded_future_start,
):
    created = partner_with_schedule
    booking_resource = default_booking_resource_for_partner(created.partner)

    update_schedule_settings(
        actor=created.owner,
        partner=created.partner,
        confirmation_mode="manual",
    )
    create_availability_window(
        actor=created.owner,
        partner=created.partner,
        booking_resource=booking_resource,
        start_at=rounded_future_start,
        end_at=rounded_future_start + timedelta(hours=2),
    )
    setup_status = get_partner_setup_status(created.partner)

    assert SetupStep.BOOKING_CONFIRMATION_MODE not in setup_status.missing_steps
    assert SetupStep.AVAILABILITY_WINDOW not in setup_status.missing_steps


@pytest.mark.django_db
def test_slot_generation_uses_availability_windows(
    partner_with_schedule,
    rounded_future_start,
):
    created = partner_with_schedule
    booking_resource = default_booking_resource_for_partner(created.partner)
    schedule_settings = update_schedule_settings(
        actor=created.owner,
        partner=created.partner,
        confirmation_mode="manual",
        slot_step_minutes=30,
        booking_horizon_days=7,
    )
    create_availability_window(
        actor=created.owner,
        partner=created.partner,
        booking_resource=booking_resource,
        start_at=rounded_future_start,
        end_at=rounded_future_start + timedelta(hours=2),
    )

    slots = generate_available_slots(
        partner=created.partner,
        booking_resource=booking_resource,
        duration_minutes=60,
        range_start=rounded_future_start,
        range_end=rounded_future_start + timedelta(hours=3),
        schedule_settings=schedule_settings,
        now=rounded_future_start - timedelta(hours=1),
    )

    assert [(slot.start_at, slot.end_at, slot.is_near_fit) for slot in slots] == [
        (rounded_future_start, rounded_future_start + timedelta(hours=1), False),
        (
            rounded_future_start + timedelta(minutes=30),
            rounded_future_start + timedelta(minutes=90),
            False,
        ),
        (
            rounded_future_start + timedelta(hours=1),
            rounded_future_start + timedelta(hours=2),
            False,
        ),
    ]


@pytest.mark.django_db
def test_blocked_time_removes_overlapping_slots(
    partner_with_schedule,
    rounded_future_start,
):
    created = partner_with_schedule
    booking_resource = default_booking_resource_for_partner(created.partner)
    schedule_settings = update_schedule_settings(
        actor=created.owner,
        partner=created.partner,
        confirmation_mode="manual",
        slot_step_minutes=30,
        booking_horizon_days=7,
    )
    create_availability_window(
        actor=created.owner,
        partner=created.partner,
        booking_resource=booking_resource,
        start_at=rounded_future_start,
        end_at=rounded_future_start + timedelta(hours=2),
    )
    create_blocked_time(
        actor=created.owner,
        partner=created.partner,
        booking_resource=booking_resource,
        start_at=rounded_future_start + timedelta(minutes=30),
        end_at=rounded_future_start + timedelta(minutes=45),
        reason="break",
    )

    slots = generate_available_slots(
        partner=created.partner,
        booking_resource=booking_resource,
        duration_minutes=60,
        range_start=rounded_future_start,
        range_end=rounded_future_start + timedelta(hours=3),
        schedule_settings=schedule_settings,
        now=rounded_future_start - timedelta(hours=1),
    )

    assert [(slot.start_at, slot.end_at) for slot in slots] == [
        (
            rounded_future_start + timedelta(hours=1),
            rounded_future_start + timedelta(hours=2),
        )
    ]


@pytest.mark.django_db
def test_block_after_range_end_still_blocks_service_crossing_boundary(
    partner_with_schedule,
    rounded_future_start,
):
    created = partner_with_schedule
    booking_resource = default_booking_resource_for_partner(created.partner)
    range_end = rounded_future_start.replace(hour=0) + timedelta(days=1)
    window_start = range_end - timedelta(minutes=30)
    window_end = range_end + timedelta(minutes=30)
    schedule_settings = update_schedule_settings(
        actor=created.owner,
        partner=created.partner,
        confirmation_mode="manual",
        slot_step_minutes=30,
        booking_horizon_days=7,
    )
    create_availability_window(
        actor=created.owner,
        partner=created.partner,
        booking_resource=booking_resource,
        start_at=window_start,
        end_at=window_end,
    )
    create_blocked_time(
        actor=created.owner,
        partner=created.partner,
        booking_resource=booking_resource,
        start_at=range_end + timedelta(minutes=15),
        end_at=range_end + timedelta(minutes=20),
        reason="after midnight",
    )

    slots = generate_available_slots(
        partner=created.partner,
        booking_resource=booking_resource,
        duration_minutes=60,
        range_start=window_start,
        range_end=range_end,
        schedule_settings=schedule_settings,
        now=window_start - timedelta(hours=1),
    )

    assert slots == ()


@pytest.mark.django_db
def test_booking_horizon_limits_generated_slots(
    partner_with_schedule,
    rounded_future_start,
):
    created = partner_with_schedule
    booking_resource = default_booking_resource_for_partner(created.partner)
    schedule_settings = update_schedule_settings(
        actor=created.owner,
        partner=created.partner,
        confirmation_mode="manual",
        booking_horizon_days=1,
    )
    future_start = rounded_future_start + timedelta(days=10)
    create_availability_window(
        actor=created.owner,
        partner=created.partner,
        booking_resource=booking_resource,
        start_at=future_start,
        end_at=future_start + timedelta(hours=2),
    )

    slots = generate_available_slots(
        partner=created.partner,
        booking_resource=booking_resource,
        duration_minutes=60,
        range_start=future_start,
        range_end=future_start + timedelta(hours=2),
        schedule_settings=schedule_settings,
        now=rounded_future_start,
    )

    assert slots == ()


@pytest.mark.django_db
def test_multi_service_duration_is_used_for_slots(partner_with_schedule, rounded_future_start):
    created = partner_with_schedule
    booking_resource = default_booking_resource_for_partner(created.partner)
    category = create_service_category(
        actor=created.owner,
        partner=created.partner,
        name="Nails",
    )
    first_service = create_service(
        actor=created.owner,
        partner=created.partner,
        category=category,
        name="Manicure",
        price_amount=50000,
        duration_minutes=30,
    )
    second_service = create_service(
        actor=created.owner,
        partner=created.partner,
        category=category,
        name="Design",
        price_amount=20000,
        duration_minutes=45,
    )
    schedule_settings = update_schedule_settings(
        actor=created.owner,
        partner=created.partner,
        confirmation_mode="manual",
        slot_step_minutes=30,
        booking_horizon_days=7,
    )
    create_availability_window(
        actor=created.owner,
        partner=created.partner,
        booking_resource=booking_resource,
        start_at=rounded_future_start,
        end_at=rounded_future_start + timedelta(hours=3),
    )

    slots = generate_available_slots(
        partner=created.partner,
        booking_resource=booking_resource,
        duration_minutes=duration_minutes_for_services([first_service, second_service]),
        range_start=rounded_future_start,
        range_end=rounded_future_start + timedelta(hours=3),
        schedule_settings=schedule_settings,
        now=rounded_future_start - timedelta(hours=1),
    )

    assert len(slots) == 4
    assert slots[0].end_at == rounded_future_start + timedelta(minutes=75)


@pytest.mark.django_db
def test_near_fit_extends_only_past_availability_end_and_never_over_busy_time(
    partner_with_schedule,
    rounded_future_start,
):
    created = partner_with_schedule
    booking_resource = default_booking_resource_for_partner(created.partner)
    schedule_settings = update_schedule_settings(
        actor=created.owner,
        partner=created.partner,
        confirmation_mode="manual",
        slot_step_minutes=60,
        booking_horizon_days=7,
        near_fit_tolerance_minutes=10,
    )
    create_availability_window(
        actor=created.owner,
        partner=created.partner,
        booking_resource=booking_resource,
        start_at=rounded_future_start,
        end_at=rounded_future_start + timedelta(hours=1),
    )

    slots = generate_available_slots(
        partner=created.partner,
        booking_resource=booking_resource,
        duration_minutes=70,
        range_start=rounded_future_start,
        range_end=rounded_future_start + timedelta(hours=2),
        schedule_settings=schedule_settings,
        now=rounded_future_start - timedelta(hours=1),
    )

    assert len(slots) == 1
    assert slots[0].is_near_fit is True

    busy_slots = generate_available_slots(
        partner=created.partner,
        booking_resource=booking_resource,
        duration_minutes=70,
        range_start=rounded_future_start,
        range_end=rounded_future_start + timedelta(hours=2),
        schedule_settings=schedule_settings,
        busy_intervals=[
            TimeInterval(
                rounded_future_start + timedelta(minutes=65),
                rounded_future_start + timedelta(minutes=66),
            )
        ],
        now=rounded_future_start - timedelta(hours=1),
    )

    assert busy_slots == ()


@pytest.mark.django_db
def test_normal_services_reject_past_windows(partner_with_schedule):
    created = partner_with_schedule
    booking_resource = default_booking_resource_for_partner(created.partner)
    start_at = timezone.now() - timedelta(hours=1)

    with pytest.raises(ValidationError):
        create_availability_window(
            actor=created.owner,
            partner=created.partner,
            booking_resource=booking_resource,
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
        )


@pytest.mark.django_db
def test_template_application_creates_concrete_windows(
    partner_with_schedule,
    rounded_future_start,
):
    created = partner_with_schedule
    booking_resource = default_booking_resource_for_partner(created.partner)
    template = create_availability_template(
        actor=created.owner,
        partner=created.partner,
        name="Evenings",
    )
    target_date = rounded_future_start.date()
    create_availability_template_window(
        actor=created.owner,
        template=template,
        weekday=target_date.weekday(),
        start_time=time(18, 0),
        end_time=time(21, 0),
    )

    windows = apply_availability_template(
        actor=created.owner,
        partner=created.partner,
        template=template,
        booking_resource=booking_resource,
        start_date=target_date,
        end_date=target_date,
    )

    assert len(windows) == 1
    assert windows[0].source_template == template
    assert windows[0].booking_resource == booking_resource


@pytest.mark.django_db
def test_partner_owner_cannot_use_other_partner_booking_resource(rounded_future_start):
    superuser = User.objects.create_superuser(username="root", password="secret")
    first = create_partner_with_owner(
        actor=superuser,
        partner_name="First",
        owner_username="first-owner",
        owner_password="secret",
    )
    second = create_partner_with_owner(
        actor=superuser,
        partner_name="Second",
        owner_username="second-owner",
        owner_password="secret",
    )
    second_resource = default_booking_resource_for_partner(second.partner)

    with pytest.raises(PermissionDenied):
        create_availability_window(
            actor=first.owner,
            partner=second.partner,
            booking_resource=second_resource,
            start_at=rounded_future_start,
            end_at=rounded_future_start + timedelta(hours=1),
        )


@pytest.mark.django_db
def test_template_window_cannot_move_between_partner_scopes() -> None:
    superuser = User.objects.create_superuser(username="root", password="secret")
    first = create_partner_with_owner(
        actor=superuser,
        partner_name="First",
        owner_username="first-owner",
        owner_password="secret",
    )
    second = create_partner_with_owner(
        actor=superuser,
        partner_name="Second",
        owner_username="second-owner",
        owner_password="secret",
    )
    first_template = create_availability_template(
        actor=first.owner,
        partner=first.partner,
        name="First template",
    )
    second_template = create_availability_template(
        actor=second.owner,
        partner=second.partner,
        name="Second template",
    )
    PartnerMembership.objects.create(
        user=first.owner,
        partner=second.partner,
        role=PartnerMembership.Role.OWNER,
    )
    template_window = create_availability_template_window(
        actor=first.owner,
        template=first_template,
        weekday=0,
        start_time=time(9, 0),
        end_time=time(12, 0),
    )

    with pytest.raises(ValidationError):
        update_availability_template_window(
            actor=first.owner,
            template_window=template_window,
            template=second_template,
            weekday=0,
            start_time=time(10, 0),
            end_time=time(13, 0),
        )

    template_window.refresh_from_db()
    assert template_window.template == first_template
