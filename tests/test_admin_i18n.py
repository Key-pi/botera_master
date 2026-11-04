import pytest
from django.conf import settings
from django.contrib.staticfiles import finders
from django.urls import reverse

from apps.accounts.models import User
from apps.partners.models import Partner


def test_admin_language_stylesheet_is_discoverable() -> None:
    assert finders.find("core/admin_language.css") is not None


@pytest.fixture(autouse=True)
def production_default_language(settings) -> None:
    settings.LANGUAGE_CODE = "uk"


@pytest.fixture
def localized_admin(client):
    superuser = User.objects.create_superuser(username="i18n-root", password="secret")
    client.force_login(superuser)
    return client


@pytest.mark.django_db
def test_admin_defaults_to_ukrainian_even_for_english_browser(localized_admin) -> None:
    response = localized_admin.get(
        reverse("admin:index"),
        HTTP_ACCEPT_LANGUAGE="en-US,en;q=0.9",
    )

    assert response.status_code == 200
    assert response.wsgi_request.LANGUAGE_CODE == "uk"
    assert "Адміністрування BoteraMaster" in response.content.decode()
    assert "Мова інтерфейсу" in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("language", "expected_heading", "expected_module"),
    [
        ("ru", "Администрирование BoteraMaster", "Записи"),
        ("en", "BoteraMaster administration", "Appointments"),
        ("uk", "Адміністрування BoteraMaster", "Записи"),
    ],
)
def test_admin_language_selection_is_saved_in_cookie(
    localized_admin,
    language: str,
    expected_heading: str,
    expected_module: str,
) -> None:
    admin_url = reverse("admin:index")

    switch_response = localized_admin.post(
        reverse("set_language"),
        {"language": language, "next": admin_url},
    )

    assert switch_response.status_code == 302
    assert switch_response.url == admin_url
    assert switch_response.cookies[settings.LANGUAGE_COOKIE_NAME].value == language

    response = localized_admin.get(admin_url)
    content = response.content.decode()
    assert response.wsgi_request.LANGUAGE_CODE == language
    assert expected_heading in content
    assert expected_module in content
    assert f'<option value="{language}" selected>' in content


@pytest.mark.django_db
def test_invalid_language_cookie_falls_back_to_ukrainian(localized_admin) -> None:
    localized_admin.cookies[settings.LANGUAGE_COOKIE_NAME] = "de"

    response = localized_admin.get(reverse("admin:index"))

    assert response.wsgi_request.LANGUAGE_CODE == "uk"
    assert "Адміністрування BoteraMaster" in response.content.decode()


@pytest.mark.django_db
def test_interface_language_does_not_change_partner_bot_language(localized_admin) -> None:
    partner = Partner.objects.create(
        name="Language-independent partner",
        default_language="en",
    )

    localized_admin.post(
        reverse("set_language"),
        {"language": "ru", "next": reverse("admin:index")},
    )

    partner.refresh_from_db()
    assert partner.default_language == "en"
