from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.partners.services import create_partner_with_owner
from apps.schedule.models import (
    AvailabilityTemplate,
    AvailabilityTemplateWindow,
    AvailabilityWindow,
    BlockedTime,
    BookingResource,
    PartnerScheduleSettings,
)


@pytest.fixture
def schedule_admin_context(client):
    superuser = User.objects.create_superuser(username="root", password="secret")
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Schedule Admin",
        owner_username="schedule-admin-owner",
        owner_password="secret",
    )
    resource = BookingResource.objects.get(partner=created.partner, is_default=True)
    client.force_login(created.owner)
    return client, created, resource


@pytest.mark.django_db
def test_partner_owner_manages_blocked_time_through_service_backed_admin(
    schedule_admin_context,
) -> None:
    client, created, resource = schedule_admin_context
    start_at = _future_local_hour(days=2)
    end_at = start_at + timedelta(hours=2)

    response = client.post(
        reverse("admin:schedule_blockedtime_add"),
        {
            "partner": str(created.partner.id),
            "booking_resource": str(resource.id),
            **_split_datetime_fields("start_at", start_at),
            **_split_datetime_fields("end_at", end_at),
            "reason": "Personal time",
            "_save": "Save",
        },
    )

    assert response.status_code == 302
    blocked_time = BlockedTime.objects.get(partner=created.partner)
    assert blocked_time.created_by == created.owner
    assert blocked_time.reason == "Personal time"

    changed_start = start_at + timedelta(hours=1)
    changed_end = end_at + timedelta(hours=1)
    response = client.post(
        reverse("admin:schedule_blockedtime_change", args=[blocked_time.id]),
        {
            "booking_resource": str(resource.id),
            **_split_datetime_fields("start_at", changed_start),
            **_split_datetime_fields("end_at", changed_end),
            "reason": "Updated personal time",
            "_save": "Save",
        },
    )

    assert response.status_code == 302
    blocked_time.refresh_from_db()
    assert blocked_time.start_at == changed_start
    assert blocked_time.end_at == changed_end
    assert blocked_time.reason == "Updated personal time"

    response = client.post(
        reverse("admin:schedule_blockedtime_delete", args=[blocked_time.id]),
        {"post": "yes"},
    )

    assert response.status_code == 302
    assert BlockedTime.objects.filter(id=blocked_time.id).exists() is False


@pytest.mark.django_db
def test_partner_owner_updates_schedule_settings_through_admin(
    schedule_admin_context,
) -> None:
    client, created, _resource = schedule_admin_context
    settings = PartnerScheduleSettings.objects.get(partner=created.partner)

    response = client.post(
        reverse("admin:schedule_partnerschedulesettings_change", args=[settings.id]),
        {
            "confirmation_mode": PartnerScheduleSettings.ConfirmationMode.MANUAL,
            "slot_step_minutes": "30",
            "booking_horizon_days": "90",
            "near_fit_tolerance_minutes": "10",
            "cancellation_deadline_hours": "12",
            "_save": "Save",
        },
    )

    assert response.status_code == 302
    settings.refresh_from_db()
    assert settings.confirmation_mode == PartnerScheduleSettings.ConfirmationMode.MANUAL
    assert settings.slot_step_minutes == 30
    assert settings.booking_horizon_days == 90
    assert settings.cancellation_deadline_hours == 12


@pytest.mark.django_db
def test_partner_owner_applies_template_idempotently_and_keeps_generated_windows(
    schedule_admin_context,
) -> None:
    client, created, resource = schedule_admin_context
    target_date = timezone.localdate() + timedelta(days=3)

    response = client.post(
        reverse("admin:schedule_availabilitytemplate_add"),
        {
            "partner": str(created.partner.id),
            "name": "Evenings",
            "is_active": "on",
            "sort_order": "0",
            "windows-TOTAL_FORMS": "0",
            "windows-INITIAL_FORMS": "0",
            "windows-MIN_NUM_FORMS": "0",
            "windows-MAX_NUM_FORMS": "0",
            "_save": "Save",
        },
    )

    assert response.status_code == 302
    template = AvailabilityTemplate.objects.get(partner=created.partner)

    response = client.post(
        reverse("admin:schedule_availabilitytemplatewindow_add"),
        {
            "template": str(template.id),
            "weekday": str(target_date.weekday()),
            "start_time": "18:00:00",
            "end_time": "21:00:00",
            "_save": "Save",
        },
    )

    assert response.status_code == 302
    assert AvailabilityTemplateWindow.objects.filter(template=template).count() == 1

    action_payload = {
        "action": "apply_selected_template",
        "_selected_action": str(template.id),
        "select_across": "0",
        "confirm_apply": "1",
        "booking_resource": str(resource.id),
        "start_date": target_date.isoformat(),
        "end_date": target_date.isoformat(),
    }
    changelist_url = reverse("admin:schedule_availabilitytemplate_changelist")

    confirmation = client.post(
        changelist_url,
        {
            "action": "apply_selected_template",
            "_selected_action": str(template.id),
            "select_across": "0",
        },
    )
    assert confirmation.status_code == 200
    assert b"Apply availability template" in confirmation.content

    assert client.post(changelist_url, action_payload).status_code == 302
    assert client.post(changelist_url, action_payload).status_code == 302
    window = AvailabilityWindow.objects.get(
        partner=created.partner,
        source_template=template,
    )
    assert (
        AvailabilityWindow.objects.filter(
            partner=created.partner,
            source_template=template,
        ).count()
        == 1
    )

    response = client.post(
        reverse("admin:schedule_availabilitytemplate_delete", args=[template.id]),
        {"post": "yes"},
    )

    assert response.status_code == 302
    window.refresh_from_db()
    assert window.source_template is None


def _future_local_hour(*, days: int):
    return timezone.localtime(timezone.now() + timedelta(days=days)).replace(
        minute=0,
        second=0,
        microsecond=0,
    )


def _split_datetime_fields(prefix: str, value) -> dict[str, str]:
    return {
        f"{prefix}_0": value.date().isoformat(),
        f"{prefix}_1": value.time().isoformat(),
    }
