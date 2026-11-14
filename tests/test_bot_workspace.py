from datetime import time
from urllib.parse import urlencode

import pytest
from cryptography.fernet import Fernet
from django.urls import reverse

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.bot.models import BotInstance, PartnerTelegramContactLink, PartnerTemplateOverride
from apps.bot.services import (
    decrypt_bot_token,
    render_content_template,
    set_bot_token,
    set_partner_template_override,
)
from apps.bot.telegram import TelegramBotIdentity
from apps.finance.models import PartnerPenaltySettings
from apps.notifications.models import PartnerNotificationSettings, ReminderRule
from apps.partners.services import create_partner_with_owner
from apps.schedule.models import PartnerScheduleSettings

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


def _workspace_url(*, partner_id, tab="instance", key="", language="") -> str:
    params = {"partner": partner_id, "tab": tab}
    if key:
        params["key"] = key
    if language:
        params["language"] = language
    return f"{reverse('bot:workspace')}?{urlencode(params)}"


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
    assert not context["first"].owner.has_perm("notifications.change_partnernotificationsettings")

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
    notification_content = notifications.content.decode()
    assert 'name="quiet_hours_start_local"' in notification_content
    assert 'value="22:00"' in notification_content
    assert 'name="quiet_hours_end_local"' in notification_content
    assert 'value="08:00"' in notification_content
    assert "Appointment reminders" in notification_content
    assert booking.status_code == 200
    assert "Booking horizon" in booking.content.decode()
    assert penalties.status_code == 200
    penalty_content = penalties.content.decode()
    assert 'name="late_cancel_penalty"' in penalty_content
    assert 'value="50.00"' in penalty_content


@pytest.mark.django_db
def test_partner_settings_workspace_updates_defaults_and_audit(bot_workspace_context) -> None:
    context = bot_workspace_context
    partner = context["first"].partner

    response = context["http_client"].post(
        _workspace_url(partner_id=partner.id, tab="settings"),
        {
            "action": "save_partner_settings",
            "default_language": "ru",
            "timezone": "Europe/Warsaw",
        },
    )

    assert response.status_code == 302
    partner.refresh_from_db()
    assert partner.default_language == "ru"
    assert partner.timezone == "Europe/Warsaw"
    assert partner.status == partner.Status.SETUP_REQUIRED
    event = AuditEvent.objects.get(
        partner=partner,
        event_type="partner.settings_updated",
    )
    assert event.actor == context["first"].owner
    assert event.payload_masked == {
        "previous": {
            "name": "Workspace Studio",
            "timezone": "Europe/Kyiv",
            "default_language": "uk",
        },
        "current": {
            "name": "Workspace Studio",
            "timezone": "Europe/Warsaw",
            "default_language": "ru",
        },
    }


@pytest.mark.django_db
def test_partner_settings_workspace_rejects_unknown_timezone_without_mutation(
    bot_workspace_context,
) -> None:
    context = bot_workspace_context
    partner = context["first"].partner

    response = context["http_client"].post(
        _workspace_url(partner_id=partner.id, tab="settings"),
        {
            "action": "save_partner_settings",
            "default_language": "en",
            "timezone": "Mars/Olympus",
        },
    )

    assert response.status_code == 200
    partner.refresh_from_db()
    assert partner.default_language == "uk"
    assert partner.timezone == "Europe/Kyiv"
    assert not AuditEvent.objects.filter(
        partner=partner,
        event_type="partner.settings_updated",
    ).exists()


@pytest.mark.django_db
def test_notifications_workspace_saves_partner_timing_and_audit(bot_workspace_context) -> None:
    context = bot_workspace_context
    partner = context["first"].partner
    url = _workspace_url(partner_id=partner.id, tab="notifications")

    response = context["http_client"].post(
        url,
        {
            "action": "save_notification_settings",
            "quiet_hours_enabled": "on",
            "quiet_hours_start_local": "23:15",
            "quiet_hours_end_local": "07:30",
            "morning_digest_time_local": "09:10",
            "stale_requested_base_interval_minutes": "95",
        },
    )

    assert response.status_code == 302
    settings = PartnerNotificationSettings.objects.get(partner=partner)
    assert settings.quiet_hours_enabled is True
    assert settings.quiet_hours_start_local == time(23, 15)
    assert settings.quiet_hours_end_local == time(7, 30)
    assert settings.morning_digest_enabled is False
    assert settings.morning_digest_time_local == time(9, 10)
    assert settings.stale_requested_base_interval_minutes == 95
    event = AuditEvent.objects.get(
        partner=partner,
        event_type="notification.settings_updated",
    )
    assert event.actor == context["first"].owner
    assert event.payload_masked["quiet_hours_start_local"] == "23:15"
    assert event.payload_masked["morning_digest_enabled"] is False


@pytest.mark.django_db
def test_notifications_workspace_replaces_active_rules_without_deleting_history(
    bot_workspace_context,
) -> None:
    context = bot_workspace_context
    partner = context["first"].partner
    url = _workspace_url(partner_id=partner.id, tab="notifications")

    response = context["http_client"].post(
        url,
        {
            "action": "save_reminder_rules",
            "client_1_value": "1",
            "client_1_unit": "days",
            "client_2_value": "3",
            "client_2_unit": "hours",
            "client_3_value": "",
            "client_3_unit": "minutes",
            "client_4_value": "",
            "client_4_unit": "minutes",
            "client_5_value": "",
            "client_5_unit": "minutes",
            "master_enabled": "on",
            "master_value": "30",
            "master_unit": "minutes",
        },
    )

    assert response.status_code == 302
    active_rules = set(
        ReminderRule.objects.filter(partner=partner, is_active=True).values_list(
            "target",
            "offset_minutes",
        )
    )
    assert active_rules == {
        (ReminderRule.Target.CLIENT, 24 * 60),
        (ReminderRule.Target.CLIENT, 3 * 60),
        (ReminderRule.Target.MASTER, 30),
    }
    assert ReminderRule.objects.filter(
        partner=partner,
        target=ReminderRule.Target.CLIENT,
        offset_minutes=2 * 60,
        is_active=False,
    ).exists()
    assert ReminderRule.objects.filter(
        partner=partner,
        target=ReminderRule.Target.MASTER,
        offset_minutes=45,
        is_active=False,
    ).exists()
    event = AuditEvent.objects.get(
        partner=partner,
        event_type="notification.reminder_rules_updated",
    )
    assert event.payload_masked == {
        "client_offsets_minutes": [1440, 180],
        "master_enabled": True,
        "master_offset_minutes": 30,
    }


@pytest.mark.django_db
def test_notifications_workspace_rejects_out_of_bounds_rules_without_mutation(
    bot_workspace_context,
) -> None:
    context = bot_workspace_context
    partner = context["first"].partner
    before = set(
        ReminderRule.objects.filter(partner=partner, is_active=True).values_list(
            "target",
            "offset_minutes",
        )
    )

    response = context["http_client"].post(
        _workspace_url(partner_id=partner.id, tab="notifications"),
        {
            "action": "save_reminder_rules",
            "client_1_value": "1",
            "client_1_unit": "minutes",
            "client_2_value": "",
            "client_2_unit": "minutes",
            "client_3_value": "",
            "client_3_unit": "minutes",
            "client_4_value": "",
            "client_4_unit": "minutes",
            "client_5_value": "",
            "client_5_unit": "minutes",
            "master_enabled": "on",
            "master_value": "45",
            "master_unit": "minutes",
        },
    )

    assert response.status_code == 200
    assert "Client reminders must be between 15 minutes and 30 days." in (response.content.decode())
    assert (
        set(
            ReminderRule.objects.filter(partner=partner, is_active=True).values_list(
                "target",
                "offset_minutes",
            )
        )
        == before
    )
    assert not AuditEvent.objects.filter(
        partner=partner,
        event_type="notification.reminder_rules_updated",
    ).exists()


@pytest.mark.django_db
def test_booking_workspace_saves_complete_rules_and_audit(bot_workspace_context) -> None:
    context = bot_workspace_context
    partner = context["first"].partner

    response = context["http_client"].post(
        _workspace_url(partner_id=partner.id, tab="booking"),
        {
            "action": "save_booking_settings",
            "confirmation_mode": PartnerScheduleSettings.ConfirmationMode.AUTO,
            "slot_step_minutes": "20",
            "booking_horizon_days": "180",
            "near_fit_tolerance_minutes": "12",
            "cancellation_deadline_hours": "8",
            "require_phone_for_booking": "on",
        },
    )

    assert response.status_code == 302
    settings = PartnerScheduleSettings.objects.get(partner=partner)
    assert settings.confirmation_mode == PartnerScheduleSettings.ConfirmationMode.AUTO
    assert settings.slot_step_minutes == 20
    assert settings.booking_horizon_days == 180
    assert settings.near_fit_tolerance_minutes == 12
    assert settings.cancellation_deadline_hours == 8
    assert settings.require_phone_for_booking is True
    event = AuditEvent.objects.get(
        partner=partner,
        event_type="schedule.settings_updated",
    )
    assert event.actor == context["first"].owner
    assert event.payload_masked == {
        "confirmation_mode": PartnerScheduleSettings.ConfirmationMode.AUTO,
        "slot_step_minutes": 20,
        "booking_horizon_days": 180,
        "near_fit_tolerance_minutes": 12,
        "cancellation_deadline_hours": 8,
        "required_booking_fields": ["phone"],
    }


@pytest.mark.django_db
def test_booking_workspace_rejects_invalid_bounds_without_mutation(
    bot_workspace_context,
) -> None:
    context = bot_workspace_context
    partner = context["first"].partner
    before = PartnerScheduleSettings.objects.get(partner=partner)
    expected = (
        before.confirmation_mode,
        before.slot_step_minutes,
        before.booking_horizon_days,
        before.near_fit_tolerance_minutes,
        before.cancellation_deadline_hours,
        before.require_phone_for_booking,
    )

    response = context["http_client"].post(
        _workspace_url(partner_id=partner.id, tab="booking"),
        {
            "action": "save_booking_settings",
            "confirmation_mode": PartnerScheduleSettings.ConfirmationMode.MANUAL,
            "slot_step_minutes": "15",
            "booking_horizon_days": "366",
            "near_fit_tolerance_minutes": "10",
            "cancellation_deadline_hours": "6",
        },
    )

    assert response.status_code == 200
    before.refresh_from_db()
    assert (
        before.confirmation_mode,
        before.slot_step_minutes,
        before.booking_horizon_days,
        before.near_fit_tolerance_minutes,
        before.cancellation_deadline_hours,
        before.require_phone_for_booking,
    ) == expected
    assert not AuditEvent.objects.filter(
        partner=partner,
        event_type="schedule.settings_updated",
    ).exists()


@pytest.mark.django_db
def test_penalty_workspace_saves_major_amounts_as_minor_units_and_audit(
    bot_workspace_context,
) -> None:
    context = bot_workspace_context
    partner = context["first"].partner

    response = context["http_client"].post(
        _workspace_url(partner_id=partner.id, tab="penalties"),
        {
            "action": "save_penalty_settings",
            "penalties_enabled": "on",
            "late_cancel_penalty": "65.50",
            "no_show_penalty": "75.00",
            "late_arrival_threshold_minutes": "20",
            "late_arrival_penalty": "25.25",
        },
    )

    assert response.status_code == 302
    settings = PartnerPenaltySettings.objects.get(partner=partner)
    assert settings.penalties_enabled is True
    assert settings.late_cancel_penalty_amount == 6_550
    assert settings.no_show_penalty_amount == 7_500
    assert settings.late_arrival_threshold_minutes == 20
    assert settings.late_arrival_penalty_amount == 2_525
    assert settings.currency == "UAH"
    event = AuditEvent.objects.get(
        partner=partner,
        event_type="penalty.settings_updated",
    )
    assert event.actor == context["first"].owner
    assert event.payload_masked == {
        "penalties_enabled": True,
        "late_cancel_penalty_amount": 6_550,
        "no_show_penalty_amount": 7_500,
        "late_arrival_threshold_minutes": 20,
        "late_arrival_penalty_amount": 2_525,
        "currency": "UAH",
    }


@pytest.mark.django_db
def test_penalty_workspace_rejects_invalid_amount_without_mutation(
    bot_workspace_context,
) -> None:
    context = bot_workspace_context
    partner = context["first"].partner
    settings = PartnerPenaltySettings.objects.get(partner=partner)
    expected = (
        settings.penalties_enabled,
        settings.late_cancel_penalty_amount,
        settings.no_show_penalty_amount,
        settings.late_arrival_threshold_minutes,
        settings.late_arrival_penalty_amount,
    )

    response = context["http_client"].post(
        _workspace_url(partner_id=partner.id, tab="penalties"),
        {
            "action": "save_penalty_settings",
            "penalties_enabled": "on",
            "late_cancel_penalty": "-1.00",
            "no_show_penalty": "75.00",
            "late_arrival_threshold_minutes": "20",
            "late_arrival_penalty": "25.00",
        },
    )

    assert response.status_code == 200
    settings.refresh_from_db()
    assert (
        settings.penalties_enabled,
        settings.late_cancel_penalty_amount,
        settings.no_show_penalty_amount,
        settings.late_arrival_threshold_minutes,
        settings.late_arrival_penalty_amount,
    ) == expected
    assert not AuditEvent.objects.filter(
        partner=partner,
        event_type="penalty.settings_updated",
    ).exists()


@pytest.mark.django_db
def test_content_workspace_shows_registry_defaults_without_foreign_overrides(
    bot_workspace_context,
) -> None:
    context = bot_workspace_context
    set_partner_template_override(
        actor=context["second"].owner,
        partner=context["second"].partner,
        key="start.welcome",
        language="uk",
        body="FOREIGN CONTENT {partner_name}",
    )

    response = context["http_client"].get(
        _workspace_url(
            partner_id=context["first"].partner.id,
            tab="content",
            key="start.welcome",
            language="uk",
        )
    )
    content = response.content.decode()

    assert response.status_code == 200
    assert "Start and welcome" in content
    assert "start.welcome" in content
    assert "Вітаємо у боті {partner_name}." in content
    assert "{partner_name}" in content
    assert "System default" in content
    assert ">UK<" in content
    assert ">RU<" in content
    assert ">EN<" in content
    assert "FOREIGN CONTENT" not in content


@pytest.mark.django_db
def test_content_workspace_saves_valid_override_and_safe_audit(bot_workspace_context) -> None:
    context = bot_workspace_context
    partner = context["first"].partner
    body = "PRIVATE CUSTOM COPY for {partner_name}"
    url = _workspace_url(
        partner_id=partner.id,
        tab="content",
        key="start.welcome",
        language="en",
    )

    response = context["http_client"].post(
        url,
        {"action": "save_content", "body": body},
    )

    assert response.status_code == 302
    override = PartnerTemplateOverride.objects.get(
        partner=partner,
        key="start.welcome",
        language="en",
    )
    assert override.body == body
    rendered = render_content_template(
        partner=partner,
        key="start.welcome",
        language="en",
        context={"partner_name": "Workspace Studio"},
    )
    assert rendered.body == "PRIVATE CUSTOM COPY for Workspace Studio"
    event = AuditEvent.objects.get(
        partner=partner,
        event_type="bot.content_override_saved",
    )
    assert event.payload_masked == {
        "template_key": "start.welcome",
        "language": "en",
        "content_type": "message",
    }
    assert body not in event.message
    assert body not in str(event.payload_masked)


@pytest.mark.django_db
def test_content_workspace_previews_without_saving_and_rejects_unknown_placeholder(
    bot_workspace_context,
) -> None:
    context = bot_workspace_context
    partner = context["first"].partner
    url = _workspace_url(
        partner_id=partner.id,
        tab="content",
        key="start.welcome",
        language="en",
    )

    preview = context["http_client"].post(
        url,
        {"action": "preview_content", "body": "Draft for {partner_name}"},
    )
    preview_content = preview.content.decode()

    assert preview.status_code == 200
    assert "Draft preview" in preview_content
    assert "Draft for &lt;partner_name&gt;" in preview_content
    assert not PartnerTemplateOverride.objects.filter(partner=partner).exists()

    invalid = context["http_client"].post(
        url,
        {"action": "save_content", "body": "Bad {unknown}"},
    )

    assert invalid.status_code == 200
    assert "Unknown placeholder(s): unknown." in invalid.content.decode()
    assert not PartnerTemplateOverride.objects.filter(partner=partner).exists()


@pytest.mark.django_db
def test_content_workspace_resets_override_to_language_default(bot_workspace_context) -> None:
    context = bot_workspace_context
    partner = context["first"].partner
    set_partner_template_override(
        actor=context["first"].owner,
        partner=partner,
        key="start.welcome",
        language="ru",
        body="Своя версия для {partner_name}",
    )
    url = _workspace_url(
        partner_id=partner.id,
        tab="content",
        key="start.welcome",
        language="ru",
    )

    response = context["http_client"].post(url, {"action": "reset_content"})

    assert response.status_code == 302
    assert not PartnerTemplateOverride.objects.filter(
        partner=partner,
        key="start.welcome",
        language="ru",
    ).exists()
    rendered = render_content_template(
        partner=partner,
        key="start.welcome",
        language="ru",
        context={"partner_name": "Workspace Studio"},
    )
    assert rendered.body == "Добро пожаловать в бот Workspace Studio."
    assert AuditEvent.objects.filter(
        partner=partner,
        event_type="bot.content_override_reset",
        payload_masked__template_key="start.welcome",
        payload_masked__language="ru",
    ).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("key", "language"),
    (("booking.slot_taken", "uk"), ("start.welcome", "de"), ("missing.key", "uk")),
)
def test_content_workspace_rejects_system_only_unknown_keys_and_languages(
    bot_workspace_context,
    key,
    language,
) -> None:
    context = bot_workspace_context

    response = context["http_client"].get(
        _workspace_url(
            partner_id=context["first"].partner.id,
            tab="content",
            key=key,
            language=language,
        )
    )

    assert response.status_code == 404


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
    assert "Europe/Kyiv" in content


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
