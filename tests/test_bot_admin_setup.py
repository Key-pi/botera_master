from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.bot.admin import (
    BotInstanceAdmin,
    PartnerTelegramContactAdmin,
    PartnerTelegramContactLinkAdmin,
)
from apps.bot.forms import BotInstanceAdminForm
from apps.bot.models import BotInstance, PartnerTelegramContact, PartnerTelegramContactLink
from apps.bot.services import activate_partner_bot, decrypt_bot_token, set_bot_token
from apps.catalog.models import Service, ServiceCategory
from apps.partners.admin import PartnerAdmin
from apps.partners.models import Partner
from apps.partners.services import create_partner_with_owner
from apps.schedule.models import AvailabilityWindow, PartnerScheduleSettings
from apps.schedule.selectors import default_booking_resource_for_partner

VALID_TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"


@pytest.fixture
def token_settings(settings):
    settings.BOT_TOKEN_ENCRYPTION_KEYS = {"main": Fernet.generate_key().decode("utf-8")}
    settings.BOT_TOKEN_CURRENT_KEY_ID = "main"
    settings.TOKEN_FINGERPRINT_KEY = "admin-setup-fingerprint-secret"
    return settings


@pytest.fixture
def partner_with_owner(token_settings):
    superuser = User.objects.create_superuser(username="root", password="secret")
    return create_partner_with_owner(
        actor=superuser,
        partner_name="Admin Setup Partner",
        owner_username="admin-setup-owner",
        owner_password="secret",
    )


@pytest.mark.django_db
def test_bot_admin_form_saves_token_through_encrypted_service(partner_with_owner) -> None:
    created = partner_with_owner
    form = BotInstanceAdminForm(data={"partner": str(created.partner.id), "new_token": VALID_TOKEN})
    assert form.is_valid(), form.errors
    bot_instance = form.save(commit=False)
    request = RequestFactory().post("/admin/bot/botinstance/add/")
    request.user = created.owner

    BotInstanceAdmin(BotInstance, AdminSite()).save_model(
        request,
        bot_instance,
        form,
        change=False,
    )

    stored = BotInstance.objects.get(partner=created.partner)
    assert bot_instance.pk == stored.pk
    assert stored.encrypted_token != VALID_TOKEN
    assert stored.token_masked == "123456789:***fghi"
    assert decrypt_bot_token(stored) == VALID_TOKEN


@pytest.mark.django_db
def test_bot_admin_add_view_accepts_token_without_rendering_it_back(
    client,
    partner_with_owner,
) -> None:
    created = partner_with_owner
    superuser = User.objects.get(username="root")
    client.force_login(superuser)
    add_url = reverse("admin:bot_botinstance_add")

    add_response = client.get(add_url)
    assert add_response.status_code == 200
    assert b'name="new_token"' in add_response.content

    save_response = client.post(
        add_url,
        {
            "partner": str(created.partner.id),
            "new_token": VALID_TOKEN,
            "_save": "Save",
        },
    )

    assert save_response.status_code == 302
    bot_instance = BotInstance.objects.get(partner=created.partner)
    change_response = client.get(reverse("admin:bot_botinstance_change", args=[bot_instance.pk]))
    assert change_response.status_code == 200
    assert VALID_TOKEN.encode() not in change_response.content


@pytest.mark.django_db
def test_bot_admin_form_never_renders_saved_raw_token(partner_with_owner) -> None:
    created = partner_with_owner
    bot_instance = set_bot_token(
        actor=created.owner,
        partner=created.partner,
        raw_token=VALID_TOKEN,
    )

    form = BotInstanceAdminForm(instance=bot_instance)

    assert form.fields["new_token"].widget.render_value is False
    assert VALID_TOKEN not in form.as_p()


@pytest.mark.django_db
def test_bot_admin_partner_choice_is_tenant_scoped(partner_with_owner) -> None:
    first = partner_with_owner
    second = create_partner_with_owner(
        actor=User.objects.get(username="root"),
        partner_name="Other Partner",
        owner_username="other-owner",
        owner_password="secret",
    )
    request = RequestFactory().get("/admin/bot/botinstance/add/")
    request.user = first.owner
    admin_adapter = BotInstanceAdmin(BotInstance, AdminSite())

    field = admin_adapter.formfield_for_foreignkey(
        BotInstance._meta.get_field("partner"),
        request,
    )

    assert list(field.queryset) == [first.partner]
    assert second.partner not in field.queryset


@pytest.mark.django_db
def test_bot_admin_form_rejects_token_already_used_by_another_partner(
    partner_with_owner,
) -> None:
    first = partner_with_owner
    second = create_partner_with_owner(
        actor=User.objects.get(username="root"),
        partner_name="Other Partner",
        owner_username="other-owner",
        owner_password="secret",
    )
    set_bot_token(actor=first.owner, partner=first.partner, raw_token=VALID_TOKEN)

    form = BotInstanceAdminForm(data={"partner": str(second.partner.id), "new_token": VALID_TOKEN})

    assert form.is_valid() is False
    assert "already used" in str(form.errors["new_token"])


@pytest.mark.django_db
def test_activate_partner_bot_requires_successful_connection_check(partner_with_owner) -> None:
    created = partner_with_owner
    bot_instance = set_bot_token(
        actor=created.owner,
        partner=created.partner,
        raw_token=VALID_TOKEN,
    )

    with pytest.raises(ValidationError, match="Check the Telegram connection"):
        activate_partner_bot(actor=created.owner, bot_instance=bot_instance)

    created.partner.refresh_from_db()
    bot_instance.refresh_from_db()
    assert created.partner.status == Partner.Status.SETUP_REQUIRED
    assert bot_instance.status == BotInstance.Status.PAUSED


@pytest.mark.django_db
def test_activate_partner_bot_launches_only_after_complete_setup(partner_with_owner) -> None:
    created = partner_with_owner
    partner = created.partner
    category = ServiceCategory.objects.create(partner=partner, name="Nails")
    Service.objects.create(
        partner=partner,
        category=category,
        name="Manicure",
        price_amount=50000,
        duration_minutes=60,
    )
    booking_resource = default_booking_resource_for_partner(partner)
    assert booking_resource is not None
    AvailabilityWindow.objects.create(
        partner=partner,
        booking_resource=booking_resource,
        start_at=timezone.now() + timedelta(days=1),
        end_at=timezone.now() + timedelta(days=1, hours=3),
    )
    PartnerScheduleSettings.objects.filter(partner=partner).update(
        confirmation_mode=PartnerScheduleSettings.ConfirmationMode.MANUAL,
    )
    bot_instance = set_bot_token(
        actor=created.owner,
        partner=partner,
        raw_token=VALID_TOKEN,
    )
    bot_instance.telegram_bot_id = 123456789
    bot_instance.telegram_username = "admin_setup_bot"
    bot_instance.last_checked_at = timezone.now()
    bot_instance.save(update_fields=["telegram_bot_id", "telegram_username", "last_checked_at"])

    launched = activate_partner_bot(actor=created.owner, bot_instance=bot_instance)

    partner.refresh_from_db()
    assert partner.status == Partner.Status.ACTIVE
    assert launched.status == BotInstance.Status.ACTIVE


@pytest.mark.django_db
def test_partner_status_and_signed_contact_rows_cannot_be_bypassed_in_admin(
    partner_with_owner,
) -> None:
    request = RequestFactory().get("/admin/")
    request.user = partner_with_owner.owner

    partner_admin = PartnerAdmin(Partner, AdminSite())
    contact_admin = PartnerTelegramContactAdmin(PartnerTelegramContact, AdminSite())
    link_admin = PartnerTelegramContactLinkAdmin(PartnerTelegramContactLink, AdminSite())

    assert "status" in partner_admin.get_readonly_fields(request, partner_with_owner.partner)
    assert partner_admin.has_add_permission(request) is False
    assert contact_admin.has_add_permission(request) is False
    assert link_admin.has_add_permission(request) is False


@pytest.mark.django_db
def test_superuser_can_open_partner_add_view_with_protected_status(
    client,
    partner_with_owner,
) -> None:
    superuser = User.objects.get(username="root")
    client.force_login(superuser)

    response = client.get(reverse("admin:partners_partner_add"))

    assert response.status_code == 200
    assert b'name="status"' not in response.content
