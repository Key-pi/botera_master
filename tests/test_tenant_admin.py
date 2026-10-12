import pytest
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import RequestFactory
from django.urls import reverse

from apps.accounts.models import PartnerMembership, User
from apps.accounts.selectors import user_can_access_partner
from apps.accounts.services import ensure_partner_access
from apps.partners.admin import PartnerAdmin
from apps.partners.models import Partner
from apps.partners.selectors import visible_partners_for_user
from apps.partners.services import activate_partner_after_setup, create_partner_with_owner
from apps.partners.setup import SetupStep, get_partner_setup_status, is_partner_runtime_eligible


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
