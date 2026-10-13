from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.bot.models import BotInstance, PartnerTelegramContact, PartnerTelegramContactLink
from apps.bot.selectors import primary_master_telegram_contact
from apps.bot.services import (
    bind_master_contact_from_start_payload,
    create_master_contact_link,
    set_bot_token,
)
from apps.broadcasts.models import BroadcastRecipient
from apps.broadcasts.services import create_broadcast_campaign, queue_broadcast_campaign
from apps.broadcasts.tasks import dispatch_due_recipients_task
from apps.clients.services import (
    resolve_client_profile_for_telegram_identity,
    upsert_telegram_identity,
)
from apps.partners.services import create_partner_with_owner

VALID_TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"


@pytest.fixture
def bot_token_settings(settings):
    key = Fernet.generate_key().decode("utf-8")
    settings.BOT_TOKEN_ENCRYPTION_KEYS = {"main": key}
    settings.BOT_TOKEN_CURRENT_KEY_ID = "main"
    settings.TOKEN_FINGERPRINT_KEY = "fingerprint-secret"
    return settings


@pytest.fixture
def partner_with_bot_username():
    superuser = User.objects.create_superuser(username="root", password="secret")
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Pilot Partner",
        owner_username="pilot-owner",
        owner_password="secret",
    )
    BotInstance.objects.create(
        partner=created.partner,
        status=BotInstance.Status.PAUSED,
        telegram_username="PilotMasterBot",
    )
    return created


@pytest.mark.django_db
def test_master_contact_link_is_short_signed_and_does_not_store_raw_payload(
    partner_with_bot_username,
):
    created = partner_with_bot_username

    link = create_master_contact_link(actor=created.owner, partner=created.partner)
    stored = PartnerTelegramContactLink.objects.get(partner=created.partner)

    assert link.url == f"https://t.me/PilotMasterBot?start={link.start_payload}"
    assert link.start_payload.startswith("admin_")
    assert len(link.start_payload) <= 64
    assert link.start_payload not in stored.nonce_fingerprint
    assert stored.user == created.owner
    assert stored.consumed_at is None


@pytest.mark.django_db
def test_signed_link_binds_primary_master_telegram_contact(partner_with_bot_username):
    created = partner_with_bot_username
    link = create_master_contact_link(actor=created.owner, partner=created.partner)
    identity = upsert_telegram_identity(
        telegram_user_id=555001,
        username="master",
        first_name="Ira",
    )

    contact = bind_master_contact_from_start_payload(
        partner=created.partner,
        start_payload=link.start_payload,
        telegram_identity=identity,
    )

    assert contact.partner == created.partner
    assert contact.telegram_identity == identity
    assert contact.user == created.owner
    assert contact.role == PartnerTelegramContact.Role.OWNER_MASTER
    assert contact.is_primary is True
    assert contact.is_active is True
    assert primary_master_telegram_contact(created.partner) == contact
    assert PartnerTelegramContactLink.objects.get(partner=created.partner).consumed_at is not None


@pytest.mark.django_db
def test_master_contact_link_cannot_be_reused(partner_with_bot_username):
    created = partner_with_bot_username
    link = create_master_contact_link(actor=created.owner, partner=created.partner)
    identity = upsert_telegram_identity(telegram_user_id=555002, username="master")

    bind_master_contact_from_start_payload(
        partner=created.partner,
        start_payload=link.start_payload,
        telegram_identity=identity,
    )

    with pytest.raises(ValidationError, match="already used"):
        bind_master_contact_from_start_payload(
            partner=created.partner,
            start_payload=link.start_payload,
            telegram_identity=identity,
        )


@pytest.mark.django_db
def test_normal_client_start_payload_does_not_bind_master_contact(partner_with_bot_username):
    created = partner_with_bot_username
    identity = upsert_telegram_identity(telegram_user_id=555003, username="client")

    with pytest.raises(ValidationError, match="Unsupported"):
        bind_master_contact_from_start_payload(
            partner=created.partner,
            start_payload="client_start",
            telegram_identity=identity,
        )

    assert PartnerTelegramContact.objects.filter(partner=created.partner).count() == 0


@pytest.mark.django_db
def test_expired_master_contact_link_is_rejected(partner_with_bot_username):
    created = partner_with_bot_username
    now = timezone.now()
    link = create_master_contact_link(actor=created.owner, partner=created.partner, now=now)
    identity = upsert_telegram_identity(telegram_user_id=555004, username="master")

    with pytest.raises(ValidationError, match="expired"):
        bind_master_contact_from_start_payload(
            partner=created.partner,
            start_payload=link.start_payload,
            telegram_identity=identity,
            now=now + timedelta(days=2),
        )


@pytest.mark.django_db
def test_broadcast_task_dispatches_through_telegram_adapter(
    bot_token_settings,
    partner_with_bot_username,
    monkeypatch,
):
    created = partner_with_bot_username
    bot_instance = set_bot_token(
        actor=created.owner,
        partner=created.partner,
        raw_token=VALID_TOKEN,
    )
    bot_instance.telegram_username = "PilotMasterBot"
    bot_instance.save(update_fields=["telegram_username"])
    identity = upsert_telegram_identity(telegram_user_id=555005, username="client")
    resolve_client_profile_for_telegram_identity(
        partner=created.partner,
        telegram_identity=identity,
    )
    campaign = create_broadcast_campaign(
        actor=created.owner,
        partner=created.partner,
        text="Hello clients",
        buttons=[{"type": "book", "label": "Book"}],
    )
    queue_broadcast_campaign(actor=created.owner, campaign=campaign)
    sent_payloads = []

    async def fake_send(token, chat_id, text, photo="", reply_markup=None):
        sent_payloads.append(
            {
                "token": token,
                "chat_id": chat_id,
                "text": text,
                "photo": photo,
                "reply_markup": reply_markup,
            }
        )

    monkeypatch.setattr("apps.bot.delivery._send_telegram_payload", fake_send)

    processed = dispatch_due_recipients_task()

    recipient = BroadcastRecipient.objects.get(campaign=campaign)
    assert processed == 1
    assert recipient.status == BroadcastRecipient.Status.SENT
    assert sent_payloads[0]["token"] == VALID_TOKEN
    assert sent_payloads[0]["chat_id"] == 555005
    assert sent_payloads[0]["text"] == "Hello clients"
    assert sent_payloads[0]["reply_markup"].inline_keyboard[0][0].callback_data == "broadcast:book"
