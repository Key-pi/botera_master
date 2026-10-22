import pytest
from django.core.exceptions import ValidationError

from apps.accounts.models import User
from apps.bot.messaging import master_client_message_markup, queue_master_conversation_reply
from apps.bot.routers import _master_message_result_from_callback_data
from apps.clients.services import (
    resolve_client_profile_for_telegram_identity,
    upsert_telegram_identity,
)
from apps.messaging.models import ConversationMessage
from apps.messaging.services import create_client_message_to_master, create_master_reply_to_client
from apps.partners.services import create_partner_with_owner


@pytest.fixture
def bot_messaging_context():
    superuser = User.objects.create_superuser(
        username="bot-messaging-root",
        password="secret",
    )
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Bot Messaging Partner",
        owner_username="bot-messaging-owner",
        owner_password="secret",
    )
    identity = upsert_telegram_identity(
        telegram_user_id=992001,
        username="messaging-client",
        first_name="Ira",
    )
    client = resolve_client_profile_for_telegram_identity(
        partner=created.partner,
        telegram_identity=identity,
    )
    source = create_client_message_to_master(
        partner=created.partner,
        client_profile=client,
        text="Could we change the booking time?",
    )
    return {
        "superuser": superuser,
        "partner": created.partner,
        "owner": created.owner,
        "client": client,
        "source": source,
    }


@pytest.mark.django_db
def test_client_message_delivery_markup_has_compact_master_reply_action(
    bot_messaging_context,
):
    source = bot_messaging_context["source"]

    markup = master_client_message_markup(message_id=source.id, language="en")

    button = markup.inline_keyboard[0][0]
    assert button.text == "Reply"
    assert button.callback_data == f"mm:reply:{source.id.hex}"
    assert len(button.callback_data.encode("utf-8")) <= 64


@pytest.mark.django_db
def test_verified_master_callback_prompts_and_queues_reply(bot_messaging_context):
    context = bot_messaging_context
    source = context["source"]

    callback_result = _master_message_result_from_callback_data(
        partner=context["partner"],
        actor=context["owner"],
        data=f"mm:reply:{source.id.hex}",
    )
    queued_reply = queue_master_conversation_reply(
        partner=context["partner"],
        actor=context["owner"],
        source_message_id=source.id,
        text="  Yes, I will suggest another time.  ",
    )

    assert callback_result.wait_for_text is True
    assert callback_result.source_message_id == source.id
    assert "Ira" in callback_result.reply.text
    assert (
        callback_result.reply.reply_markup.inline_keyboard[0][0].callback_data
        == f"mm:cancel:{source.id.hex}"
    )
    reply = ConversationMessage.objects.get(
        partner=context["partner"],
        direction=ConversationMessage.Direction.MASTER_TO_CLIENT,
    )
    assert reply.client_profile == context["client"]
    assert reply.created_by == context["owner"]
    assert reply.text == "Yes, I will suggest another time."
    assert reply.delivery_status == ConversationMessage.DeliveryStatus.QUEUED
    assert "Ira" in queued_reply.text


@pytest.mark.django_db
def test_master_message_callback_cannot_cross_partner_boundary(bot_messaging_context):
    context = bot_messaging_context
    other = create_partner_with_owner(
        actor=context["superuser"],
        partner_name="Other Messaging Partner",
        owner_username="other-messaging-owner",
        owner_password="secret",
    )

    with pytest.raises(ValidationError):
        _master_message_result_from_callback_data(
            partner=other.partner,
            actor=other.owner,
            data=f"mm:reply:{context['source'].id.hex}",
        )

    assert (
        ConversationMessage.objects.filter(
            direction=ConversationMessage.Direction.MASTER_TO_CLIENT,
        ).count()
        == 0
    )


@pytest.mark.django_db
def test_master_cannot_use_outgoing_reply_as_new_client_message_source(
    bot_messaging_context,
):
    context = bot_messaging_context
    outgoing = create_master_reply_to_client(
        actor=context["owner"],
        client_profile=context["client"],
        text="Existing outgoing reply",
    )

    with pytest.raises(ValidationError):
        _master_message_result_from_callback_data(
            partner=context["partner"],
            actor=context["owner"],
            data=f"mm:reply:{outgoing.id.hex}",
        )
