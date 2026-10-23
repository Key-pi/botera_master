import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.clients.services import (
    create_manual_client_profile,
    resolve_client_profile_for_telegram_identity,
    upsert_telegram_identity,
)
from apps.messaging.models import ConversationMessage
from apps.partners.services import create_partner_with_owner


@pytest.fixture
def messaging_admin_context():
    superuser = User.objects.create_superuser(username="messaging-root", password="secret")
    first = create_partner_with_owner(
        actor=superuser,
        partner_name="First Messaging Partner",
        owner_username="first-messaging-owner",
        owner_password="secret",
    )
    second = create_partner_with_owner(
        actor=superuser,
        partner_name="Second Messaging Partner",
        owner_username="second-messaging-owner",
        owner_password="secret",
    )
    first_client = resolve_client_profile_for_telegram_identity(
        partner=first.partner,
        telegram_identity=upsert_telegram_identity(
            telegram_user_id=991001,
            username="first-client",
            first_name="Ira",
        ),
    )
    second_client = resolve_client_profile_for_telegram_identity(
        partner=second.partner,
        telegram_identity=upsert_telegram_identity(
            telegram_user_id=991002,
            username="second-client",
            first_name="Other",
        ),
    )
    manual_client = create_manual_client_profile(
        actor=first.owner,
        partner=first.partner,
        name="Manual client",
    )
    return {
        "first": first,
        "second": second,
        "first_client": first_client,
        "second_client": second_client,
        "manual_client": manual_client,
    }


@pytest.mark.django_db
def test_partner_owner_replies_to_own_client_through_service_backed_admin(
    client,
    messaging_admin_context,
):
    context = messaging_admin_context
    owner = context["first"].owner
    client.force_login(owner)
    reply_url = reverse(
        "admin:messaging_conversationmessage_reply",
        args=[context["first_client"].id],
    )

    assert owner.has_perm("messaging.add_conversationmessage") is True
    assert client.get(reply_url).status_code == 200

    response = client.post(reply_url, {"text": "  See you tomorrow.  "})

    message = ConversationMessage.objects.get(partner=context["first"].partner)
    assert response.status_code == 302
    assert response.url == reverse(
        "admin:messaging_conversationmessage_change",
        args=[message.id],
    )
    assert message.client_profile == context["first_client"]
    assert message.created_by == owner
    assert message.direction == ConversationMessage.Direction.MASTER_TO_CLIENT
    assert message.text == "See you tomorrow."
    assert message.delivery_status == ConversationMessage.DeliveryStatus.QUEUED
    assert AuditEvent.objects.filter(
        partner=context["first"].partner,
        target_id=str(message.id),
        event_type="messaging.message_created",
    ).exists()
    detail_response = client.get(response.url)
    assert detail_response.status_code == 200
    assert reply_url in detail_response.content.decode()


@pytest.mark.django_db
def test_partner_owner_cannot_reply_to_another_partners_client(
    client,
    messaging_admin_context,
):
    context = messaging_admin_context
    client.force_login(context["first"].owner)
    reply_url = reverse(
        "admin:messaging_conversationmessage_reply",
        args=[context["second_client"].id],
    )

    assert client.get(reply_url).status_code == 404
    assert client.post(reply_url, {"text": "Cross-tenant message"}).status_code == 404
    assert ConversationMessage.objects.count() == 0


@pytest.mark.django_db
def test_client_card_offers_message_action_only_for_linked_client(
    client,
    messaging_admin_context,
):
    context = messaging_admin_context
    client.force_login(context["first"].owner)
    linked_change_url = reverse(
        "admin:clients_clientprofile_change",
        args=[context["first_client"].id],
    )
    manual_change_url = reverse(
        "admin:clients_clientprofile_change",
        args=[context["manual_client"].id],
    )
    reply_url = reverse(
        "admin:messaging_conversationmessage_reply",
        args=[context["first_client"].id],
    )

    linked_response = client.get(linked_change_url)
    manual_response = client.get(manual_change_url)

    assert linked_response.status_code == 200
    assert reply_url in linked_response.content.decode()
    assert manual_response.status_code == 200
    assert "Message client" not in manual_response.content.decode()


@pytest.mark.django_db
def test_direct_conversation_message_add_is_disabled(client, messaging_admin_context):
    context = messaging_admin_context
    client.force_login(context["first"].owner)

    response = client.get(reverse("admin:messaging_conversationmessage_add"))

    assert response.status_code == 403
