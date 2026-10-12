import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import User
from apps.bot.models import BotInstance
from apps.bot.runtime import load_runtime_bot_configs
from apps.bot.services import (
    decrypt_bot_token,
    mark_bot_error,
    set_bot_status,
    set_bot_token,
)
from apps.partners.models import Partner
from apps.partners.services import create_partner_with_owner
from apps.partners.setup import SetupStep, get_partner_setup_status

VALID_TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
FUTURE_SETUP_STEPS = {
    SetupStep.CATALOG_SERVICE,
    SetupStep.AVAILABILITY_WINDOW,
    SetupStep.BOOKING_CONFIRMATION_MODE,
}


@pytest.fixture
def bot_token_settings(settings):
    key = Fernet.generate_key().decode("utf-8")
    settings.BOT_TOKEN_ENCRYPTION_KEYS = {"main": key}
    settings.BOT_TOKEN_CURRENT_KEY_ID = "main"
    settings.TOKEN_FINGERPRINT_KEY = "fingerprint-secret"
    return settings


@pytest.fixture
def partner_with_owner(bot_token_settings):
    superuser = User.objects.create_superuser(username="root", password="secret")
    return create_partner_with_owner(
        actor=superuser,
        partner_name="Runtime Partner",
        owner_username="runtime-owner",
        owner_password="secret",
    )


@pytest.mark.django_db
def test_set_bot_token_encrypts_masks_and_completes_bot_token_setup_step(partner_with_owner):
    created = partner_with_owner

    bot_instance = set_bot_token(
        actor=created.owner,
        partner=created.partner,
        raw_token=VALID_TOKEN,
    )
    setup_status = get_partner_setup_status(created.partner)

    assert bot_instance.status == BotInstance.Status.PAUSED
    assert bot_instance.encrypted_token != VALID_TOKEN
    assert bot_instance.token_key_id == "main"
    assert bot_instance.token_masked == "123456789:***fghi"
    assert decrypt_bot_token(bot_instance) == VALID_TOKEN
    assert SetupStep.BOT_TOKEN not in setup_status.missing_steps


@pytest.mark.django_db
def test_invalid_bot_token_format_is_rejected(partner_with_owner):
    created = partner_with_owner

    with pytest.raises(ValidationError):
        set_bot_token(actor=created.owner, partner=created.partner, raw_token="not-a-token")


@pytest.mark.django_db
def test_duplicate_bot_token_is_rejected(partner_with_owner):
    created = partner_with_owner
    second = create_partner_with_owner(
        actor=User.objects.get(username="root"),
        partner_name="Second",
        owner_username="second-owner",
        owner_password="secret",
    )
    set_bot_token(actor=created.owner, partner=created.partner, raw_token=VALID_TOKEN)

    with pytest.raises(ValidationError):
        set_bot_token(actor=second.owner, partner=second.partner, raw_token=VALID_TOKEN)


@pytest.mark.django_db
def test_partner_owner_cannot_suspend_bot_but_superuser_can(partner_with_owner):
    created = partner_with_owner
    bot_instance = set_bot_token(
        actor=created.owner,
        partner=created.partner,
        raw_token=VALID_TOKEN,
    )

    with pytest.raises(PermissionDenied):
        set_bot_status(
            actor=created.owner,
            bot_instance=bot_instance,
            status=BotInstance.Status.SUSPENDED,
        )

    superuser = User.objects.get(username="root")
    set_bot_status(
        actor=superuser,
        bot_instance=bot_instance,
        status=BotInstance.Status.SUSPENDED,
    )
    bot_instance.refresh_from_db()

    assert bot_instance.status == BotInstance.Status.SUSPENDED
    with pytest.raises(PermissionDenied):
        set_bot_status(
            actor=created.owner,
            bot_instance=bot_instance,
            status=BotInstance.Status.PAUSED,
        )


@pytest.mark.django_db
def test_runtime_loader_returns_active_eligible_bot_config(partner_with_owner):
    created = partner_with_owner
    created.partner.status = Partner.Status.ACTIVE
    created.partner.save(update_fields=["status"])
    bot_instance = set_bot_token(
        actor=created.owner,
        partner=created.partner,
        raw_token=VALID_TOKEN,
    )
    set_bot_status(
        actor=created.owner,
        bot_instance=bot_instance,
        status=BotInstance.Status.ACTIVE,
    )

    result = load_runtime_bot_configs(
        completed_setup_steps_by_partner={created.partner.id: FUTURE_SETUP_STEPS}
    )

    assert len(result.configs) == 1
    assert result.configs[0].token == VALID_TOKEN
    assert result.configs[0].token_masked == "123456789:***fghi"
    assert result.skipped == ()


@pytest.mark.django_db
def test_runtime_loader_skips_setup_incomplete_partner(partner_with_owner):
    created = partner_with_owner
    created.partner.status = Partner.Status.ACTIVE
    created.partner.save(update_fields=["status"])
    bot_instance = set_bot_token(
        actor=created.owner,
        partner=created.partner,
        raw_token=VALID_TOKEN,
    )
    set_bot_status(
        actor=created.owner,
        bot_instance=bot_instance,
        status=BotInstance.Status.ACTIVE,
    )

    result = load_runtime_bot_configs()

    assert result.configs == ()
    assert result.skipped[0].reason_code == "partner_not_runtime_eligible"
    assert SetupStep.CATALOG_SERVICE in result.skipped[0].detail


@pytest.mark.django_db
def test_runtime_loader_skips_inactive_bot_statuses(partner_with_owner):
    created = partner_with_owner
    created.partner.status = Partner.Status.ACTIVE
    created.partner.save(update_fields=["status"])
    set_bot_token(actor=created.owner, partner=created.partner, raw_token=VALID_TOKEN)

    result = load_runtime_bot_configs(
        completed_setup_steps_by_partner={created.partner.id: FUTURE_SETUP_STEPS}
    )

    assert result.configs == ()
    assert result.skipped == ()


@pytest.mark.django_db
def test_runtime_loader_skips_when_token_cannot_be_decrypted(settings, partner_with_owner):
    created = partner_with_owner
    created.partner.status = Partner.Status.ACTIVE
    created.partner.save(update_fields=["status"])
    bot_instance = set_bot_token(
        actor=created.owner,
        partner=created.partner,
        raw_token=VALID_TOKEN,
    )
    set_bot_status(
        actor=created.owner,
        bot_instance=bot_instance,
        status=BotInstance.Status.ACTIVE,
    )
    settings.BOT_TOKEN_ENCRYPTION_KEYS = {"main": Fernet.generate_key().decode("utf-8")}

    result = load_runtime_bot_configs(
        completed_setup_steps_by_partner={created.partner.id: FUTURE_SETUP_STEPS}
    )

    assert result.configs == ()
    assert result.skipped[0].reason_code == "token_decrypt_failed"


@pytest.mark.django_db
def test_mark_bot_error_masks_token_like_text(partner_with_owner):
    created = partner_with_owner
    bot_instance = set_bot_token(
        actor=created.owner,
        partner=created.partner,
        raw_token=VALID_TOKEN,
    )

    mark_bot_error(
        bot_instance,
        code="telegram_start_failed",
        message=f"Telegram refused token {VALID_TOKEN}",
    )
    bot_instance.refresh_from_db()

    assert bot_instance.status == BotInstance.Status.ERROR
    assert VALID_TOKEN not in bot_instance.last_error_message_masked
    assert "123456789:***fghi" in bot_instance.last_error_message_masked
