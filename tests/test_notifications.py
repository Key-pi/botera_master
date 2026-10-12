from datetime import datetime, time, timedelta
from datetime import timezone as datetime_timezone

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.appointments.models import Appointment
from apps.appointments.services import (
    AppointmentServiceItemInput,
    create_confirmed_appointment,
    create_requested_appointment,
    transition_appointment_status,
)
from apps.catalog.services import create_service, create_service_category
from apps.clients.services import (
    create_manual_client_profile,
    resolve_client_profile_for_telegram_identity,
    upsert_telegram_identity,
)
from apps.notifications.models import (
    DeliveryAttempt,
    PartnerNotificationSettings,
    ReminderRule,
    ScheduledNotification,
)
from apps.notifications.services import (
    DeliveryResult,
    apply_quiet_hours,
    dispatch_notification,
    get_or_create_notification_settings,
)
from apps.partners.services import create_partner_with_owner
from apps.schedule.selectors import default_booking_resource_for_partner
from core.timezones import get_zone


@pytest.fixture
def notification_context():
    superuser = User.objects.create_superuser(username="root", password="secret")
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Notifications Partner",
        owner_username="notifications-owner",
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
    telegram_identity = upsert_telegram_identity(telegram_user_id=12345, username="ira")
    telegram_client = resolve_client_profile_for_telegram_identity(
        partner=created.partner,
        telegram_identity=telegram_identity,
    )
    manual_client = create_manual_client_profile(
        actor=created.owner,
        partner=created.partner,
        name="Manual",
        phone="+380501111111",
    )
    return {
        "partner": created.partner,
        "owner": created.owner,
        "resource": default_booking_resource_for_partner(created.partner),
        "service": service,
        "telegram_client": telegram_client,
        "manual_client": manual_client,
    }


@pytest.fixture
def future_start():
    return (timezone.now() + timedelta(days=5)).replace(
        hour=12,
        minute=0,
        second=0,
        microsecond=0,
    )


def _service_input(service):
    return AppointmentServiceItemInput(service=service)


@pytest.mark.django_db
def test_partner_creation_initializes_notification_defaults(notification_context):
    partner = notification_context["partner"]

    assert PartnerNotificationSettings.objects.filter(partner=partner).exists()
    assert set(
        ReminderRule.objects.filter(partner=partner).values_list("target", "offset_minutes")
    ) == {
        (ReminderRule.Target.CLIENT, 24 * 60),
        (ReminderRule.Target.CLIENT, 2 * 60),
        (ReminderRule.Target.MASTER, 45),
    }


@pytest.mark.django_db
def test_confirmed_appointment_schedules_client_and_master_reminders_after_commit(
    notification_context,
    future_start,
    django_capture_on_commit_callbacks,
):
    context = notification_context

    with django_capture_on_commit_callbacks(execute=True):
        appointment = create_confirmed_appointment(
            actor=context["owner"],
            partner=context["partner"],
            booking_resource=context["resource"],
            client_profile=context["telegram_client"],
            start_at=future_start,
            service_items=[_service_input(context["service"])],
        )

    notifications = ScheduledNotification.objects.filter(appointment=appointment)

    assert notifications.count() == 3
    assert notifications.filter(
        type=ScheduledNotification.Type.CLIENT_APPOINTMENT_REMINDER
    ).count() == 2
    assert notifications.filter(
        type=ScheduledNotification.Type.MASTER_APPOINTMENT_REMINDER
    ).count() == 1


@pytest.mark.django_db
def test_manual_client_confirmed_appointment_skips_client_reminders(
    notification_context,
    future_start,
    django_capture_on_commit_callbacks,
):
    context = notification_context

    with django_capture_on_commit_callbacks(execute=True):
        appointment = create_confirmed_appointment(
            actor=context["owner"],
            partner=context["partner"],
            booking_resource=context["resource"],
            client_profile=context["manual_client"],
            start_at=future_start,
            service_items=[_service_input(context["service"])],
        )

    notifications = ScheduledNotification.objects.filter(appointment=appointment)

    assert notifications.count() == 1
    assert notifications.get().type == ScheduledNotification.Type.MASTER_APPOINTMENT_REMINDER


@pytest.mark.django_db
def test_requested_appointment_schedules_only_stale_requested_reminder(
    notification_context,
    future_start,
    django_capture_on_commit_callbacks,
):
    context = notification_context

    with django_capture_on_commit_callbacks(execute=True):
        appointment = create_requested_appointment(
            actor=None,
            partner=context["partner"],
            booking_resource=context["resource"],
            client_profile=context["telegram_client"],
            start_at=future_start,
            service_items=[_service_input(context["service"])],
        )

    notification = ScheduledNotification.objects.get(appointment=appointment)

    assert notification.type == ScheduledNotification.Type.STALE_REQUESTED
    assert notification.status == ScheduledNotification.Status.QUEUED


@pytest.mark.django_db
def test_final_status_stales_pending_reminders(
    notification_context,
    future_start,
    django_capture_on_commit_callbacks,
):
    context = notification_context
    with django_capture_on_commit_callbacks(execute=True):
        appointment = create_confirmed_appointment(
            actor=context["owner"],
            partner=context["partner"],
            booking_resource=context["resource"],
            client_profile=context["telegram_client"],
            start_at=future_start,
            service_items=[_service_input(context["service"])],
        )

    with django_capture_on_commit_callbacks(execute=True):
        transition_appointment_status(
            actor=context["owner"],
            appointment=appointment,
            new_status=Appointment.Status.COMPLETED,
        )

    assert not ScheduledNotification.objects.filter(
        appointment=appointment,
        status=ScheduledNotification.Status.QUEUED,
    ).exists()
    assert ScheduledNotification.objects.filter(
        appointment=appointment,
        status=ScheduledNotification.Status.STALE,
    ).count() == 3


@pytest.mark.django_db
def test_quiet_hours_move_due_time_to_quiet_hours_end(notification_context):
    partner = notification_context["partner"]
    settings = get_or_create_notification_settings(partner=partner)
    settings.quiet_hours_start_local = time(22, 0)
    settings.quiet_hours_end_local = time(8, 0)
    settings.save(update_fields=["quiet_hours_start_local", "quiet_hours_end_local", "updated_at"])
    partner_zone = get_zone(partner.timezone)
    due_at = datetime(2026, 10, 13, 7, 0, tzinfo=partner_zone).astimezone(
        datetime_timezone.utc
    )

    adjusted = apply_quiet_hours(due_at=due_at, partner=partner, settings=settings)

    assert adjusted.astimezone(partner_zone).time() == time(8, 0)


@pytest.mark.django_db
def test_dispatch_notification_is_idempotent(notification_context):
    context = notification_context
    calls = []
    notification = ScheduledNotification.objects.create(
        partner=context["partner"],
        type=ScheduledNotification.Type.MASTER_MORNING_DIGEST,
        due_at=timezone.now() - timedelta(minutes=1),
        idempotency_key="digest-once",
    )

    def deliver(_notification):
        calls.append(_notification.id)
        return DeliveryResult(success=True)

    first_status = dispatch_notification(notification=notification, deliver=deliver)
    second_status = dispatch_notification(notification=notification, deliver=deliver)

    assert first_status == ScheduledNotification.Status.SENT
    assert second_status == ScheduledNotification.Status.SKIPPED
    assert calls == [notification.id]
    assert DeliveryAttempt.objects.filter(notification=notification).count() == 1


@pytest.mark.django_db
def test_transient_delivery_failure_retries_then_becomes_partner_visible(
    notification_context,
):
    context = notification_context
    due_at = timezone.now() - timedelta(minutes=1)
    notification = ScheduledNotification.objects.create(
        partner=context["partner"],
        client_profile=context["telegram_client"],
        type=ScheduledNotification.Type.CLIENT_APPOINTMENT_REMINDER,
        due_at=due_at,
        idempotency_key="retry-client-reminder",
    )

    def fail(_notification):
        return DeliveryResult(success=False, failure_code="telegram_timeout")

    status_1 = dispatch_notification(notification=notification, deliver=fail, now=due_at)
    notification.refresh_from_db()
    status_2 = dispatch_notification(
        notification=notification,
        deliver=fail,
        now=notification.due_at,
    )
    notification.refresh_from_db()
    status_3 = dispatch_notification(
        notification=notification,
        deliver=fail,
        now=notification.due_at,
    )
    notification.refresh_from_db()

    assert status_1 == ScheduledNotification.Status.QUEUED
    assert status_2 == ScheduledNotification.Status.QUEUED
    assert status_3 == ScheduledNotification.Status.FAILED
    assert notification.attempt_count == 3
    assert notification.failed_visible_to_partner is True


@pytest.mark.django_db
def test_terminal_delivery_failure_marks_client_unreachable(notification_context):
    context = notification_context
    notification = ScheduledNotification.objects.create(
        partner=context["partner"],
        client_profile=context["telegram_client"],
        type=ScheduledNotification.Type.CLIENT_APPOINTMENT_REMINDER,
        due_at=timezone.now() - timedelta(minutes=1),
        idempotency_key="blocked-client-reminder",
    )

    def blocked(_notification):
        return DeliveryResult(
            success=False,
            failure_code="bot_blocked",
            failure_message="Forbidden: bot was blocked by the user",
            terminal=True,
        )

    status = dispatch_notification(notification=notification, deliver=blocked)
    context["telegram_client"].refresh_from_db()

    assert status == ScheduledNotification.Status.FAILED
    assert context["telegram_client"].bot_unreachable is True
    assert context["telegram_client"].last_delivery_failure_code == "bot_blocked"


@pytest.mark.django_db
def test_obsolete_reminder_is_skipped_without_delivery(
    notification_context,
    future_start,
):
    context = notification_context
    appointment = create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=context["telegram_client"],
        start_at=future_start,
        service_items=[_service_input(context["service"])],
    )
    now = future_start + timedelta(minutes=1)
    notification = ScheduledNotification.objects.create(
        partner=context["partner"],
        appointment=appointment,
        client_profile=context["telegram_client"],
        type=ScheduledNotification.Type.CLIENT_APPOINTMENT_REMINDER,
        due_at=now - timedelta(minutes=1),
        idempotency_key="obsolete-client-reminder",
    )

    def deliver(_notification):
        raise AssertionError("obsolete notification should not be delivered")

    status = dispatch_notification(notification=notification, deliver=deliver, now=now)
    notification.refresh_from_db()

    assert status == ScheduledNotification.Status.SKIPPED
    assert notification.status == ScheduledNotification.Status.SKIPPED


@pytest.mark.django_db
def test_sent_stale_requested_reminder_schedules_next_repeat(
    notification_context,
    future_start,
    django_capture_on_commit_callbacks,
):
    context = notification_context
    with django_capture_on_commit_callbacks(execute=True):
        appointment = create_requested_appointment(
            actor=None,
            partner=context["partner"],
            booking_resource=context["resource"],
            client_profile=context["telegram_client"],
            start_at=future_start,
            service_items=[_service_input(context["service"])],
        )
    first = ScheduledNotification.objects.get(appointment=appointment)
    first.due_at = timezone.now() - timedelta(minutes=1)
    first.save(update_fields=["due_at", "updated_at"])

    status = dispatch_notification(
        notification=first,
        deliver=lambda _notification: DeliveryResult(success=True),
    )

    assert status == ScheduledNotification.Status.SENT
    assert ScheduledNotification.objects.filter(
        appointment=appointment,
        type=ScheduledNotification.Type.STALE_REQUESTED,
        status=ScheduledNotification.Status.QUEUED,
        stale_sequence=1,
    ).exists()
