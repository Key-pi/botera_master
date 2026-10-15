from datetime import timedelta
from io import StringIO

import pytest
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import PartnerMembership, User
from apps.accounts.permissions import PARTNER_OWNER_GROUP_NAME
from apps.accounts.selectors import user_can_access_partner
from apps.accounts.services import ensure_partner_access
from apps.catalog.models import ServiceCategory
from apps.finance.models import PartnerPenaltySettings
from apps.partners.admin import PartnerAdmin
from apps.partners.models import Partner
from apps.partners.selectors import visible_partners_for_user
from apps.partners.services import activate_partner_after_setup, create_partner_with_owner
from apps.partners.setup import SetupStep, get_partner_setup_status, is_partner_runtime_eligible
from apps.schedule.models import AvailabilityWindow, BookingResource, PartnerScheduleSettings


@pytest.mark.django_db
def test_superuser_creates_setup_required_partner_with_owner_account() -> None:
    superuser = User.objects.create_superuser(username="root", password="secret")

    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Nails by Ira",
        owner_username="ira",
        owner_password="owner-secret",
        owner_email="ira@example.com",
    )

    assert created.partner.status == Partner.Status.SETUP_REQUIRED
    assert created.owner.is_staff is True
    assert created.owner.check_password("owner-secret")
    assert created.membership.role == PartnerMembership.Role.OWNER
    assert user_can_access_partner(created.owner, created.partner) is True
    assert created.owner.groups.filter(name=PARTNER_OWNER_GROUP_NAME).exists()
    assert created.owner.has_perm("partners.change_partner") is True
    assert created.owner.has_perm("bot.add_botinstance") is True
    assert created.owner.has_perm("catalog.add_service") is True
    assert created.owner.has_perm("schedule.add_availabilitywindow") is True
    assert created.owner.has_perm("appointments.add_appointment") is True
    assert created.owner.has_perm("appointments.change_appointment") is True
    assert created.owner.has_perm("clients.change_clientprofile") is True
    assert created.owner.has_perm("accounts.view_user") is False
    assert created.owner.has_perm("clients.view_telegramidentity") is False
    assert created.owner.has_perm("audit.view_platformdiagnostic") is False


@pytest.mark.django_db
def test_superuser_onboards_partner_and_owner_through_admin(client) -> None:
    superuser = User.objects.create_superuser(username="root", password="secret")
    client.force_login(superuser)

    response = client.post(
        reverse("admin:partners_partner_add"),
        {
            "name": "Fresh Pilot",
            "timezone": "Europe/Kiev",
            "default_language": "uk",
            "owner_username": "fresh-owner",
            "owner_email": "fresh@example.com",
            "owner_first_name": "Fresh",
            "owner_last_name": "Owner",
            "owner_password1": "Strong-pilot-password-482",
            "owner_password2": "Strong-pilot-password-482",
            "_save": "Save",
        },
    )

    assert response.status_code == 302
    partner = Partner.objects.get(name="Fresh Pilot")
    owner = User.objects.get(username="fresh-owner")
    assert partner.status == Partner.Status.SETUP_REQUIRED
    assert owner.check_password("Strong-pilot-password-482")
    assert owner.is_staff is True
    assert PartnerMembership.objects.filter(user=owner, partner=partner, is_active=True).exists()
    assert PartnerScheduleSettings.objects.filter(partner=partner).exists()
    assert PartnerPenaltySettings.objects.filter(partner=partner).exists()
    assert BookingResource.objects.filter(partner=partner, is_default=True).exists()
    assert owner.groups.filter(name=PARTNER_OWNER_GROUP_NAME).exists()


@pytest.mark.django_db
def test_partner_owner_admin_access_is_useful_but_excludes_platform_data(client) -> None:
    superuser = User.objects.create_superuser(username="root", password="secret")
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Scoped Owner",
        owner_username="scoped-owner",
        owner_password="secret",
    )
    client.force_login(created.owner)

    assert client.get(reverse("admin:index")).status_code == 200
    assert client.get(reverse("admin:bot_botinstance_add")).status_code == 200
    assert client.get(reverse("admin:catalog_servicecategory_add")).status_code == 200
    assert client.get(reverse("admin:schedule_availabilitywindow_add")).status_code == 200
    assert client.get(reverse("admin:appointments_appointment_add")).status_code == 200
    assert client.get(reverse("admin:accounts_user_changelist")).status_code == 403
    assert client.get(reverse("admin:audit_platformdiagnostic_changelist")).status_code == 403
    assert client.get(reverse("admin:partners_partner_add")).status_code == 403


@pytest.mark.django_db
def test_owner_access_sync_repairs_existing_owner_group_membership() -> None:
    superuser = User.objects.create_superuser(username="root", password="secret")
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Existing Owner",
        owner_username="existing-owner",
        owner_password="secret",
    )
    created.owner.groups.clear()
    created.owner.is_staff = False
    created.owner.save(update_fields=["is_staff"])
    output = StringIO()

    call_command("sync_partner_owner_access", stdout=output)

    created.owner.refresh_from_db()
    assert created.owner.is_staff is True
    assert created.owner.groups.filter(name=PARTNER_OWNER_GROUP_NAME).exists()
    assert "Synchronized 1 active partner owner account(s)." in output.getvalue()


@pytest.mark.django_db
def test_partner_owner_cannot_forge_partner_scope_in_admin_add(client) -> None:
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
    client.force_login(first.owner)

    response = client.post(
        reverse("admin:catalog_servicecategory_add"),
        {
            "partner": str(second.partner.id),
            "name": "Forged category",
            "description": "",
            "sort_order": "0",
            "is_active": "on",
            "_save": "Save",
        },
    )

    assert response.status_code == 200
    assert ServiceCategory.objects.filter(name="Forged category").exists() is False


@pytest.mark.django_db
def test_partner_owner_creates_availability_through_service_backed_admin(client) -> None:
    superuser = User.objects.create_superuser(username="root", password="secret")
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Available Owner",
        owner_username="available-owner",
        owner_password="secret",
    )
    resource = BookingResource.objects.get(partner=created.partner, is_default=True)
    start_at = timezone.localtime(timezone.now() + timedelta(days=2)).replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    end_at = start_at + timedelta(hours=2)
    client.force_login(created.owner)

    response = client.post(
        reverse("admin:schedule_availabilitywindow_add"),
        {
            "partner": str(created.partner.id),
            "booking_resource": str(resource.id),
            "start_at_0": start_at.date().isoformat(),
            "start_at_1": start_at.time().isoformat(),
            "end_at_0": end_at.date().isoformat(),
            "end_at_1": end_at.time().isoformat(),
            "source_template": "",
            "_save": "Save",
        },
    )

    assert response.status_code == 302
    window = AvailabilityWindow.objects.get(partner=created.partner)
    assert window.booking_resource == resource
    assert window.created_by == created.owner


@pytest.mark.django_db
def test_non_superuser_cannot_create_partner_with_owner_account() -> None:
    actor = User.objects.create_user(username="owner", password="secret", is_staff=True)

    with pytest.raises(PermissionDenied):
        create_partner_with_owner(
            actor=actor,
            partner_name="Blocked",
            owner_username="blocked",
            owner_password="owner-secret",
        )


@pytest.mark.django_db
def test_partner_owner_sees_only_own_partner_scope() -> None:
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

    assert list(visible_partners_for_user(first.owner)) == [first.partner]
    assert set(visible_partners_for_user(superuser)) == {first.partner, second.partner}
    assert user_can_access_partner(first.owner, second.partner) is False

    with pytest.raises(PermissionDenied):
        ensure_partner_access(first.owner, second.partner)


@pytest.mark.django_db
def test_inactive_membership_removes_partner_access() -> None:
    superuser = User.objects.create_superuser(username="root", password="secret")
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Quiet Partner",
        owner_username="owner",
        owner_password="secret",
    )

    created.membership.is_active = False
    created.membership.save(update_fields=["is_active"])

    assert list(visible_partners_for_user(created.owner)) == []
    assert user_can_access_partner(created.owner, created.partner) is False


@pytest.mark.django_db
def test_partner_admin_queryset_is_partner_scoped() -> None:
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
    request = RequestFactory().get("/admin/partners/partner/")
    request.user = first.owner

    queryset = PartnerAdmin(Partner, AdminSite()).get_queryset(request)

    assert list(queryset) == [first.partner]
    assert second.partner not in queryset


@pytest.mark.django_db
def test_partner_dashboard_placeholder_is_partner_scoped(client) -> None:
    superuser = User.objects.create_superuser(username="root", password="secret")
    first = create_partner_with_owner(
        actor=superuser,
        partner_name="First",
        owner_username="first-owner",
        owner_password="secret",
    )
    create_partner_with_owner(
        actor=superuser,
        partner_name="Second",
        owner_username="second-owner",
        owner_password="secret",
    )
    client.force_login(first.owner)

    response = client.get(reverse("partners:dashboard"))

    assert response.status_code == 200
    assert b"First" in response.content
    assert b"Second" not in response.content


@pytest.mark.django_db
def test_setup_required_partner_is_not_runtime_eligible() -> None:
    partner = Partner.objects.create(name="Setup", status=Partner.Status.SETUP_REQUIRED)

    assert is_partner_runtime_eligible(partner) is False


@pytest.mark.django_db
def test_active_partner_still_needs_minimum_external_setup_steps() -> None:
    partner = Partner.objects.create(name="Active", status=Partner.Status.ACTIVE)
    status = get_partner_setup_status(partner)

    assert status.is_complete is False
    assert set(status.missing_steps) == {
        SetupStep.BOT_TOKEN,
        SetupStep.CATALOG_SERVICE,
        SetupStep.AVAILABILITY_WINDOW,
        SetupStep.BOOKING_CONFIRMATION_MODE,
    }
    assert is_partner_runtime_eligible(partner) is False


@pytest.mark.django_db
def test_partner_can_activate_after_setup_completion() -> None:
    superuser = User.objects.create_superuser(username="root", password="secret")
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Ready",
        owner_username="ready-owner",
        owner_password="secret",
    )
    completed_steps = {
        SetupStep.BOT_TOKEN,
        SetupStep.CATALOG_SERVICE,
        SetupStep.AVAILABILITY_WINDOW,
        SetupStep.BOOKING_CONFIRMATION_MODE,
    }

    partner = activate_partner_after_setup(
        actor=created.owner,
        partner=created.partner,
        completed_external_steps=completed_steps,
    )

    assert partner.status == Partner.Status.ACTIVE
    assert is_partner_runtime_eligible(
        partner,
        completed_external_steps=completed_steps,
    )


@pytest.mark.django_db
def test_activation_requires_complete_setup() -> None:
    superuser = User.objects.create_superuser(username="root", password="secret")
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Not Ready",
        owner_username="not-ready-owner",
        owner_password="secret",
    )

    with pytest.raises(ValidationError):
        activate_partner_after_setup(actor=created.owner, partner=created.partner)
