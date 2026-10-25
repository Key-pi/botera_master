from urllib.parse import urlencode

import pytest
from cryptography.fernet import Fernet
from django.urls import reverse

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.bot.models import BotInstance, PartnerTelegramContactLink
from apps.bot.services import decrypt_bot_token, set_bot_token
from apps.bot.telegram import TelegramBotIdentity
from apps.partners.services import create_partner_with_owner

VALID_TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
SECOND_VALID_TOKEN = "987654321:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghj"


@pytest.fixture
def bot_workspace_context(client, settings):
    settings.BOT_TOKEN_ENCRYPTION_KEYS = {"main": Fernet.generate_key().decode("utf-8")}
    settings.BOT_TOKEN_CURRENT_KEY_ID = "main"
    settings.TOKEN_FINGERPRINT_KEY = "workspace-fingerprint-secret"
    superuser = User.objects.create_superuser(username="root", password="secret")
    first = create_partner_with_owner(
        actor=superuser,
        partner_name="Workspace Studio",
        owner_username="workspace-owner",
        owner_password="secret",
    )
    second = create_partner_with_owner(
        actor=superuser,
        partner_name="Foreign Workspace Studio",
        owner_username="foreign-workspace-owner",
        owner_password="secret",
    )
    client.force_login(first.owner)
    return {
        "http_client": client,
        "superuser": superuser,
        "first": first,
        "second": second,
    }


def _workspace_url(*, partner_id, tab="instance") -> str:
    return f"{reverse('bot:workspace')}?{urlencode({'partner': partner_id, 'tab': tab})}"


@pytest.mark.django_db
def test_workspace_is_tenant_scoped_and_exposes_approved_tabs(bot_workspace_context) -> None:
    context = bot_workspace_context

    response = context["http_client"].get(_workspace_url(partner_id=context["first"].partner.id))
    content = response.content.decode()

    assert response.status_code == 200
    assert "Workspace Studio" in content
    assert "Foreign Workspace Studio" not in content
    for label in ("Instance", "Settings", "Content", "Notifications", "Booking", "Penalties"):
        assert label in content
    assert "Launch checklist" in content
    assert "Not configured" in content

    foreign_response = context["http_client"].get(
        _workspace_url(partner_id=context["second"].partner.id)
    )
    assert foreign_response.status_code == 404


@pytest.mark.django_db
def test_workspace_saves_token_without_rendering_raw_value(bot_workspace_context) -> None:
    context = bot_workspace_context
    url = _workspace_url(partner_id=context["first"].partner.id)

    response = context["http_client"].post(
        url,
        {
            "action": "replace_token",
            "new_token": VALID_TOKEN,
        },
    )

    assert response.status_code == 302
    stored = BotInstance.objects.get(partner=context["first"].partner)
    assert stored.encrypted_token != VALID_TOKEN
    assert decrypt_bot_token(stored) == VALID_TOKEN
    detail = context["http_client"].get(url).content.decode()
    assert VALID_TOKEN not in detail
    assert stored.token_masked in detail

    event = AuditEvent.objects.get(
        partner=context["first"].partner,
        event_type="bot.token_updated",
    )
    assert event.actor == context["first"].owner
    assert VALID_TOKEN not in str(event.payload_masked)


@pytest.mark.django_db
def test_workspace_checks_connection_and_creates_signed_master_link(
    bot_workspace_context,
    monkeypatch,
) -> None:
    context = bot_workspace_context
    bot_instance = set_bot_token(
        actor=context["first"].owner,
        partner=context["first"].partner,
        raw_token=VALID_TOKEN,
    )

    async def fake_fetch_bot_identity(raw_token):
        assert raw_token == VALID_TOKEN
        return TelegramBotIdentity(
            telegram_bot_id=123456789,
            username="workspace_test_bot",
            first_name="Workspace",
        )

    monkeypatch.setattr("apps.bot.services.fetch_bot_identity", fake_fetch_bot_identity)
    url = _workspace_url(partner_id=context["first"].partner.id)

    checked = context["http_client"].post(url, {"action": "check_connection"})
    assert checked.status_code == 302
    bot_instance.refresh_from_db()
    assert bot_instance.telegram_username == "workspace_test_bot"
    assert AuditEvent.objects.filter(
        partner=context["first"].partner,
        event_type="bot.connection_checked",
        payload_masked__success=True,
    ).exists()

    linked = context["http_client"].post(url, {"action": "create_master_link"})
    linked_content = linked.content.decode()
    assert linked.status_code == 200
    assert "https://t.me/workspace_test_bot?start=admin_" in linked_content
    assert (
        PartnerTelegramContactLink.objects.filter(
            partner=context["first"].partner,
            user=context["first"].owner,
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_workspace_pause_action_uses_status_service_and_audit(bot_workspace_context) -> None:
    context = bot_workspace_context
    bot_instance = set_bot_token(
        actor=context["first"].owner,
        partner=context["first"].partner,
        raw_token=VALID_TOKEN,
    )
    bot_instance.status = BotInstance.Status.ACTIVE
    bot_instance.save(update_fields=["status", "updated_at"])

    response = context["http_client"].post(
        _workspace_url(partner_id=context["first"].partner.id),
        {"action": "pause"},
    )

    assert response.status_code == 302
    bot_instance.refresh_from_db()
    assert bot_instance.status == BotInstance.Status.PAUSED
    event = AuditEvent.objects.get(
        partner=context["first"].partner,
        event_type="bot.status_changed",
    )
    assert event.payload_masked == {
        "old_status": BotInstance.Status.ACTIVE,
        "new_status": BotInstance.Status.PAUSED,
    }


@pytest.mark.django_db
def test_workspace_tab_summaries_stay_partner_scoped(bot_workspace_context) -> None:
    context = bot_workspace_context
    partner = context["first"].partner

    notifications = context["http_client"].get(
        _workspace_url(partner_id=partner.id, tab="notifications")
    )
    booking = context["http_client"].get(_workspace_url(partner_id=partner.id, tab="booking"))
    penalties = context["http_client"].get(_workspace_url(partner_id=partner.id, tab="penalties"))

    assert notifications.status_code == 200
    assert "22:00–08:00" in notifications.content.decode()
    assert "Appointment reminders" in notifications.content.decode()
    assert booking.status_code == 200
    assert "Booking horizon" in booking.content.decode()
    assert penalties.status_code == 200
    assert "50.00 UAH" in penalties.content.decode()


@pytest.mark.django_db
def test_workspace_rejects_unknown_tab_and_action(bot_workspace_context) -> None:
    context = bot_workspace_context
    partner = context["first"].partner

    unknown_tab = context["http_client"].get(_workspace_url(partner_id=partner.id, tab="unknown"))
    unknown_action = context["http_client"].post(
        _workspace_url(partner_id=partner.id),
        {"action": "suspend"},
    )

    assert unknown_tab.status_code == 404
    assert unknown_action.status_code == 200
    assert "Unknown bot action." in unknown_action.content.decode()
    assert not BotInstance.objects.filter(partner=partner).exists()


@pytest.mark.django_db
def test_superuser_can_switch_visible_partner_in_workspace(bot_workspace_context) -> None:
    context = bot_workspace_context
    context["http_client"].force_login(context["superuser"])

    response = context["http_client"].get(
        _workspace_url(partner_id=context["first"].partner.id, tab="settings")
    )
    content = response.content.decode()

    assert response.status_code == 200
    assert "Workspace Studio" in content
    assert "Foreign Workspace Studio" in content
    assert "Europe/Kiev" in content


@pytest.mark.django_db
def test_workspace_rejects_duplicate_token_without_exposing_other_tenant(
    bot_workspace_context,
) -> None:
    context = bot_workspace_context
    set_bot_token(
        actor=context["second"].owner,
        partner=context["second"].partner,
        raw_token=SECOND_VALID_TOKEN,
    )
    url = _workspace_url(partner_id=context["first"].partner.id)

    response = context["http_client"].post(
        url,
        {
            "action": "replace_token",
            "new_token": SECOND_VALID_TOKEN,
        },
    )
    content = response.content.decode()

    assert response.status_code == 200
    assert "already used" in content
    assert SECOND_VALID_TOKEN not in content
    assert "Foreign Workspace Studio" not in content
