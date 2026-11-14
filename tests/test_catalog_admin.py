from decimal import Decimal

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Service, ServiceCategory
from apps.catalog.services import create_service, create_service_category
from apps.partners.services import create_partner_with_owner


@pytest.fixture
def catalog_admin_context(client):
    superuser = User.objects.create_superuser(username="root", password="secret")
    first = create_partner_with_owner(
        actor=superuser,
        partner_name="Catalog First",
        owner_username="catalog-first-owner",
        owner_password="secret",
    )
    second = create_partner_with_owner(
        actor=superuser,
        partner_name="Catalog Second",
        owner_username="catalog-second-owner",
        owner_password="secret",
    )
    active_category = create_service_category(
        actor=first.owner,
        partner=first.partner,
        name="Nails",
        description="Hands and nail care",
    )
    inactive_category = create_service_category(
        actor=first.owner,
        partner=first.partner,
        name="Seasonal",
        is_active=False,
        sort_order=10,
    )
    bookable_service = create_service(
        actor=first.owner,
        partner=first.partner,
        category=active_category,
        name="Classic manicure",
        price_amount=50_000,
        duration_minutes=90,
    )
    create_service(
        actor=first.owner,
        partner=first.partner,
        category=active_category,
        name="Archived manicure",
        price_amount=40_000,
        duration_minutes=60,
        is_active=False,
    )
    hidden_service = create_service(
        actor=first.owner,
        partner=first.partner,
        category=inactive_category,
        name="Winter care",
        price_amount=60_000,
        duration_minutes=75,
    )
    foreign_category = create_service_category(
        actor=second.owner,
        partner=second.partner,
        name="Foreign category",
    )
    foreign_service = create_service(
        actor=second.owner,
        partner=second.partner,
        category=foreign_category,
        name="Foreign service",
        price_amount=10_000,
        duration_minutes=30,
    )
    client.force_login(first.owner)
    return {
        "http_client": client,
        "superuser": superuser,
        "first": first,
        "second": second,
        "active_category": active_category,
        "inactive_category": inactive_category,
        "bookable_service": bookable_service,
        "hidden_service": hidden_service,
        "foreign_category": foreign_category,
        "foreign_service": foreign_service,
    }


@pytest.mark.django_db
def test_catalog_changelists_are_operational_and_partner_scoped(
    catalog_admin_context,
) -> None:
    context = catalog_admin_context
    client = context["http_client"]

    category_response = client.get(reverse("admin:catalog_servicecategory_changelist"))
    service_response = client.get(reverse("admin:catalog_service_changelist"))
    bookable_response = client.get(
        reverse("admin:catalog_service_changelist"),
        {"booking_state": "bookable"},
    )
    hidden_response = client.get(
        reverse("admin:catalog_service_changelist"),
        {"booking_state": "category_inactive"},
    )

    assert category_response.status_code == 200
    assert service_response.status_code == 200
    category_page = category_response.content.decode()
    service_page = service_response.content.decode()
    assert "Nails" in category_page
    assert "1 active / 2 total" in category_page
    assert "Foreign category" not in category_page
    assert "Classic manicure" in service_page
    assert "500.00 UAH" in service_page
    assert "90 min" in service_page
    assert "Bookable" in service_page
    assert "Hidden by category" in service_page
    assert "Foreign service" not in service_page
    assert "partner" not in category_response.context["cl"].list_display
    assert "partner" not in service_response.context["cl"].list_display
    assert _admin_result_ids(bookable_response) == [context["bookable_service"].id]
    assert _admin_result_ids(hidden_response) == [context["hidden_service"].id]


@pytest.mark.django_db
def test_superuser_catalog_lists_include_partner_column(catalog_admin_context) -> None:
    context = catalog_admin_context
    client = context["http_client"]
    client.force_login(context["superuser"])

    category_response = client.get(reverse("admin:catalog_servicecategory_changelist"))
    service_response = client.get(reverse("admin:catalog_service_changelist"))

    assert "partner" in category_response.context["cl"].list_display
    assert "partner" in service_response.context["cl"].list_display


@pytest.mark.django_db
def test_owner_manages_category_through_service_backed_admin(
    catalog_admin_context,
) -> None:
    context = catalog_admin_context
    client = context["http_client"]

    add_response = client.post(
        reverse("admin:catalog_servicecategory_add"),
        {
            "partner": str(context["first"].partner.id),
            "name": "  Brows  ",
            "description": "  Brow services  ",
            "sort_order": "4",
            "is_active": "on",
            "_save": "Save",
        },
    )

    assert add_response.status_code == 302
    category = ServiceCategory.objects.get(partner=context["first"].partner, name="Brows")
    assert category.description == "Brow services"

    change_response = client.post(
        reverse("admin:catalog_servicecategory_change", args=[category.id]),
        {
            "name": "Brows updated",
            "description": "Updated description",
            "sort_order": "7",
            "_save": "Save",
        },
    )

    assert change_response.status_code == 302
    category.refresh_from_db()
    assert category.name == "Brows updated"
    assert category.description == "Updated description"
    assert category.sort_order == 7
    assert category.is_active is False


@pytest.mark.django_db
def test_owner_manages_service_prices_in_major_units_through_admin(
    catalog_admin_context,
) -> None:
    context = catalog_admin_context
    client = context["http_client"]
    category = context["active_category"]

    add_response = client.post(
        reverse("admin:catalog_service_add"),
        {
            "partner": str(context["first"].partner.id),
            "category": str(category.id),
            "name": "Premium manicure",
            "description": "  Partner-defined service  ",
            "price": "650.50",
            "currency": "uah",
            "duration_minutes": "105",
            "sort_order": "5",
            "is_active": "on",
            "_save": "Save",
        },
    )

    assert add_response.status_code == 302
    service = Service.objects.get(partner=context["first"].partner, name="Premium manicure")
    assert service.price_amount == 65_050
    assert service.currency == "UAH"
    assert service.description == "Partner-defined service"

    change_page = client.get(reverse("admin:catalog_service_change", args=[service.id]))
    assert change_page.status_code == 200
    assert change_page.context["adminform"].form["price"].value() == Decimal("650.50")
    assert change_page.context["has_delete_permission"] is False

    change_response = client.post(
        reverse("admin:catalog_service_change", args=[service.id]),
        {
            "category": str(category.id),
            "name": "Premium manicure updated",
            "description": "Updated",
            "price": "700.25",
            "currency": "UAH",
            "duration_minutes": "120",
            "sort_order": "6",
            "is_active": "on",
            "_save": "Save",
        },
    )

    assert change_response.status_code == 302
    service.refresh_from_db()
    assert service.name == "Premium manicure updated"
    assert service.price_amount == 70_025
    assert service.duration_minutes == 120
    assert service.sort_order == 6


@pytest.mark.django_db
def test_owner_cannot_create_service_in_foreign_category(catalog_admin_context) -> None:
    context = catalog_admin_context
    response = context["http_client"].post(
        reverse("admin:catalog_service_add"),
        {
            "partner": str(context["first"].partner.id),
            "category": str(context["foreign_category"].id),
            "name": "Cross-tenant service",
            "description": "",
            "price": "100.00",
            "currency": "UAH",
            "duration_minutes": "30",
            "sort_order": "0",
            "is_active": "on",
            "_save": "Save",
        },
    )

    assert response.status_code == 200
    category_errors = response.context["adminform"].form.errors.as_data()["category"]
    assert category_errors[0].code == "invalid_choice"
    assert Service.objects.filter(name="Cross-tenant service").exists() is False


def _admin_result_ids(response) -> list:
    return [item.id for item in response.context["cl"].result_list]
