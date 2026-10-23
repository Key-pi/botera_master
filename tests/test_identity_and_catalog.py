import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import User
from apps.catalog.selectors import (
    active_categories_for_booking,
    active_services_for_booking,
    services_for_partner,
)
from apps.catalog.services import create_service, create_service_category
from apps.clients.selectors import client_profiles_for_partner, manual_client_link_candidates
from apps.clients.services import (
    create_manual_client_profile,
    link_client_profile_to_telegram_identity,
    mark_client_unreachable,
    resolve_client_profile_for_telegram_identity,
    set_marketing_opt_out,
    upsert_telegram_identity,
)
from apps.partners.services import create_partner_with_owner
from apps.partners.setup import SetupStep, get_partner_setup_status


@pytest.fixture
def two_partners():
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
    return first, second


@pytest.mark.django_db
def test_one_telegram_identity_can_have_separate_partner_client_profiles(two_partners):
    first, second = two_partners
    identity = upsert_telegram_identity(
        telegram_user_id=12345,
        username="client",
        first_name="Ira",
        is_premium=True,
        language_code="ru",
    )

    first_profile = resolve_client_profile_for_telegram_identity(
        partner=first.partner,
        telegram_identity=identity,
    )
    second_profile = resolve_client_profile_for_telegram_identity(
        partner=second.partner,
        telegram_identity=identity,
    )
    first_profile.phone = "+380501111111"
    first_profile.name = "First partner name"
    first_profile.save(update_fields=["phone", "name"])
    second_profile.phone = "+380502222222"
    second_profile.name = "Second partner name"
    second_profile.save(update_fields=["phone", "name"])

    assert first_profile.telegram_identity == identity
    assert second_profile.telegram_identity == identity
    assert first_profile.id != second_profile.id
    assert list(client_profiles_for_partner(first.partner)) == [first_profile]
    assert list(client_profiles_for_partner(second.partner)) == [second_profile]


@pytest.mark.django_db
def test_marketing_and_reachability_flags_are_partner_scoped(two_partners):
    first, second = two_partners
    identity = upsert_telegram_identity(telegram_user_id=12345, username="client")
    first_profile = resolve_client_profile_for_telegram_identity(
        partner=first.partner,
        telegram_identity=identity,
    )
    second_profile = resolve_client_profile_for_telegram_identity(
        partner=second.partner,
        telegram_identity=identity,
    )

    set_marketing_opt_out(client_profile=first_profile, opt_out=True)
    mark_client_unreachable(client_profile=first_profile, failure_code="bot_blocked")
    second_profile.refresh_from_db()

    assert first_profile.marketing_opt_out is True
    assert first_profile.bot_unreachable is True
    assert first_profile.last_delivery_failure_code == "bot_blocked"
    assert second_profile.marketing_opt_out is False
    assert second_profile.bot_unreachable is False

    resolve_client_profile_for_telegram_identity(
        partner=first.partner,
        telegram_identity=identity,
    )
    first_profile.refresh_from_db()

    assert first_profile.bot_unreachable is False
    assert first_profile.last_delivery_failure_code == ""


@pytest.mark.django_db
def test_manual_client_profile_can_exist_without_telegram_identity(two_partners):
    first, _second = two_partners

    profile = create_manual_client_profile(
        actor=first.owner,
        partner=first.partner,
        name="Manual Client",
        phone="+38 (050) 111-11-11",
        private_master_note="Only the master sees this",
    )

    assert profile.telegram_identity is None
    assert profile.has_telegram_identity is False
    assert profile.phone == "+380501111111"
    assert profile.private_master_note == "Only the master sees this"


@pytest.mark.django_db
def test_manual_client_link_candidates_do_not_auto_merge(two_partners):
    first, _second = two_partners
    manual = create_manual_client_profile(
        actor=first.owner,
        partner=first.partner,
        name="Phone client",
        phone="+380501111111",
    )
    identity = upsert_telegram_identity(
        telegram_user_id=98765,
        username="phone_client",
        first_name="Phone",
    )

    candidates = list(
        manual_client_link_candidates(
            partner=first.partner,
            phone="+38 (050) 111-11-11",
        )
    )

    assert candidates == [manual]
    assert manual.telegram_identity is None

    linked = link_client_profile_to_telegram_identity(
        actor=first.owner,
        client_profile=manual,
        telegram_identity=identity,
    )

    assert linked.telegram_identity == identity


@pytest.mark.django_db
def test_manual_client_link_candidates_ignore_blank_phone(two_partners):
    first, _second = two_partners
    create_manual_client_profile(
        actor=first.owner,
        partner=first.partner,
        name="Name only",
    )

    assert list(manual_client_link_candidates(partner=first.partner, phone="")) == []


@pytest.mark.django_db
def test_partner_owner_cannot_create_manual_client_outside_scope(two_partners):
    first, second = two_partners

    with pytest.raises(PermissionDenied):
        create_manual_client_profile(
            actor=first.owner,
            partner=second.partner,
            name="Blocked",
        )


@pytest.mark.django_db
def test_catalog_services_are_partner_owned_and_bookable_when_active(two_partners):
    first, _second = two_partners
    category = create_service_category(
        actor=first.owner,
        partner=first.partner,
        name="Nails",
        description="Hands and nail work",
    )
    service = create_service(
        actor=first.owner,
        partner=first.partner,
        category=category,
        name="Manicure",
        price_amount=50000,
        duration_minutes=90,
        description="Classic manicure",
    )

    assert service.currency == "UAH"
    assert service.price_amount == 50000
    assert service.duration_minutes == 90
    assert list(active_categories_for_booking(first.partner)) == [category]
    assert list(active_services_for_booking(first.partner)) == [service]
    assert SetupStep.CATALOG_SERVICE not in get_partner_setup_status(
        first.partner
    ).missing_steps


@pytest.mark.django_db
def test_inactive_category_or_service_is_hidden_from_booking_but_kept_for_admin(two_partners):
    first, _second = two_partners
    category = create_service_category(
        actor=first.owner,
        partner=first.partner,
        name="Archive",
    )
    service = create_service(
        actor=first.owner,
        partner=first.partner,
        category=category,
        name="Old service",
        price_amount=0,
        duration_minutes=30,
    )

    service.is_active = False
    service.save(update_fields=["is_active"])

    assert list(active_categories_for_booking(first.partner)) == []
    assert list(active_services_for_booking(first.partner)) == []
    assert list(services_for_partner(first.partner)) == [service]

    service.is_active = True
    service.save(update_fields=["is_active"])
    category.is_active = False
    category.save(update_fields=["is_active"])

    assert list(active_categories_for_booking(first.partner)) == []
    assert list(active_services_for_booking(first.partner)) == []
    assert list(services_for_partner(first.partner)) == [service]


@pytest.mark.django_db
def test_service_category_must_belong_to_same_partner(two_partners):
    first, second = two_partners
    other_category = create_service_category(
        actor=second.owner,
        partner=second.partner,
        name="Other",
    )

    with pytest.raises(ValidationError):
        create_service(
            actor=first.owner,
            partner=first.partner,
            category=other_category,
            name="Cross partner service",
            price_amount=10000,
            duration_minutes=30,
        )


@pytest.mark.django_db
def test_catalog_service_validation_keeps_money_and_duration_sane(two_partners):
    first, _second = two_partners
    category = create_service_category(
        actor=first.owner,
        partner=first.partner,
        name="Nails",
    )

    with pytest.raises(ValidationError):
        create_service(
            actor=first.owner,
            partner=first.partner,
            category=category,
            name="Broken price",
            price_amount=-1,
            duration_minutes=30,
        )

    with pytest.raises(ValidationError):
        create_service(
            actor=first.owner,
            partner=first.partner,
            category=category,
            name="Broken duration",
            price_amount=0,
            duration_minutes=0,
        )
