from datetime import timedelta
from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.appointments.services import (
    AppointmentServiceItemInput,
    create_confirmed_appointment,
)
from apps.bot.delivery import deliver_scheduled_notification
from apps.bot.models import BotInstance, PartnerTelegramContact
from apps.bot.notification_failures import (
    failed_delivery_to_master_markup,
    queue_failed_client_message_reply,
)
from apps.bot.routers import _failed_notification_result_from_callback_data
from apps.bot.services import set_bot_token
from apps.catalog.services import create_service, create_service_category
from apps.clients.services import (
    resolve_client_profile_for_telegram_identity,
    upsert_telegram_identity,
)
from apps.messaging.models import ConversationMessage
from apps.notifications.models import ScheduledNotification
from apps.notifications.services import DeliveryResult
from apps.partners.services import create_partner_with_owner
from apps.schedule.selectors import default_booking_resource_for_partner

VALID_TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"


@pytest.fixture
def failed_reminder_context():
    superuser = User.objects.create_superuser(
        username="failed-reminder-root",
        password="secret",
    )
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Failed Reminder Partner",
        owner_username="failed-reminder-owner",
        owner_password="secret",
    )
    category = create_service_category(
        actor=created.owner,
        partner=created.partner,
        name="Nails",
    )
    service = create_service(
        actor=created.owner,
        partner=created.partner,
        category=category,
        name="Manicure",
        price_amount=50_000,
        duration_minutes=60,
    )
    identity = upsert_telegram_identity(
        telegram_user_id=993001,
        username="failed-client",
        first_name="Ira",
    )
    client = resolve_client_profile_for_telegram_identity(
        partner=created.partner,
        telegram_identity=identity,
    )
    client.name = "Ira"
    client.phone = "+380501234567"
    client.save(update_fields=["name", "phone", "updated_at"])
    appointment = create_confirmed_appointment(
        actor=created.owner,
        partner=created.partner,
        booking_resource=default_booking_resource_for_partner(created.partner),
        client_profile=client,
        start_at=timezone.now() + timedelta(days=2),
        service_items=[AppointmentServiceItemInput(service=service)],
    )
    source = ScheduledNotification.objects.create(
        partner=created.partner,
        appointment=appointment,
        client_profile=client,
        type=ScheduledNotification.Type.CLIENT_APPOINTMENT_REMINDER,
        due_at=timezone.now(),
        status=ScheduledNotification.Status.FAILED,
        failed_visible_to_partner=True,
        last_failure_code="bot_blocked",
        last_failure_message_masked="sensitive Telegram payload",
        idempotency_key="failed-reminder-adapter-source",
    )
    return {
        "superuser": superuser,
        "partner": created.partner,
        "owner": created.owner,
        "client": client,
        "appointment": appointment,
        "source": source,
    }


@pytest.mark.django_db
def test_failed_reminder_delivery_is_safe_and_actionable(
    failed_reminder_context,
    settings,
    monkeypatch,
):
    context = failed_reminder_context
    key = Fernet.generate_key().decode("utf-8")
    settings.BOT_TOKEN_ENCRYPTION_KEYS = {"main": key}
    settings.BOT_TOKEN_CURRENT_KEY_ID = "main"
    settings.TOKEN_FINGERPRINT_KEY = "fingerprint-secret"
    BotInstance.objects.create(partner=context["partner"])
    set_bot_token(
        actor=context["owner"],
        partner=context["partner"],
        raw_token=VALID_TOKEN,
    )
    master_identity = upsert_telegram_identity(
        telegram_user_id=993002,
        username="failed-master",
    )
    PartnerTelegramContact.objects.create(
        partner=context["partner"],
        telegram_identity=master_identity,
        user=context["owner"],
        is_primary=True,
    )
    marker = ScheduledNotification.objects.create(
        partner=context["partner"],
        appointment=context["appointment"],
        client_profile=context["client"],
        source_notification=context["source"],
        type=ScheduledNotification.Type.FAILED_DELIVERY_TO_MASTER,
        due_at=timezone.now(),
        idempotency_key="failed-reminder-master-marker",
    )
    marker.refresh_from_db()
    payloads = []

    async def fake_send(token, chat_id, text, photo="", reply_markup=None):
        payloads.append(
            {
                "token": token,
                "chat_id": chat_id,
                "text": text,
                "reply_markup": reply_markup,
            }
        )

    monkeypatch.setattr("apps.bot.delivery._send_telegram_payload", fake_send)

    result = deliver_scheduled_notification(marker)

    assert result.success is True
    assert payloads[0]["chat_id"] == 993002
    assert "клієнт заблокував бота" in payloads[0]["text"]
    assert "sensitive Telegram payload" not in payloads[0]["text"]
    callbacks = [
        button.callback_data
        for row in payloads[0]["reply_markup"].inline_keyboard
        for button in row
    ]
    assert callbacks == [
        f"nf:retry:{context['source'].id.hex}",
        f"nf:message:{context['source'].id.hex}",
        f"nf:client:{context['source'].id.hex}",
    ]
    assert all(len(callback.encode("utf-8")) <= 64 for callback in callbacks)


@pytest.mark.django_db
def test_failed_reminder_retry_rechecks_tenant_and_sends_once(failed_reminder_context):
    context = failed_reminder_context
    other = create_partner_with_owner(
        actor=context["superuser"],
        partner_name="Other Failed Reminder Partner",
        owner_username="other-failed-reminder-owner",
        owner_password="secret",
    )
    callback_data = f"nf:retry:{context['source'].id.hex}"

    with pytest.raises(ValidationError):
        _failed_notification_result_from_callback_data(
            partner=other.partner,
            actor=other.owner,
            data=callback_data,
            deliver=lambda _notification: DeliveryResult(success=True),
        )

    result = _failed_notification_result_from_callback_data(
        partner=context["partner"],
        actor=context["owner"],
        data=callback_data,
        deliver=lambda _notification: DeliveryResult(success=True),
    )

    context["source"].refresh_from_db()
    assert "успішно надіслано" in result.reply.text
    assert context["source"].status == ScheduledNotification.Status.SENT
    assert context["source"].failed_visible_to_partner is False


@pytest.mark.django_db
def test_failed_reminder_client_actions_use_source_client(failed_reminder_context):
    context = failed_reminder_context
    source_id = context["source"].id

    summary = _failed_notification_result_from_callback_data(
        partner=context["partner"],
        actor=context["owner"],
        data=f"nf:client:{source_id.hex}",
    )
    prompt = _failed_notification_result_from_callback_data(
        partner=context["partner"],
        actor=context["owner"],
        data=f"nf:message:{source_id.hex}",
    )
    queued = queue_failed_client_message_reply(
        partner=context["partner"],
        actor=context["owner"],
        source_notification_id=source_id,
        text="  I will contact you here.  ",
    )

    assert summary.send_new_message is True
    assert "Ira" in summary.reply.text
    assert "+380501234567" in summary.reply.text
    assert prompt.wait_for_text is True
    assert prompt.source_notification_id == source_id
    assert f"nf:cancel:{source_id.hex}" in str(prompt.reply.reply_markup)
    assert "Ira" in queued.text
    message = ConversationMessage.objects.get(
        partner=context["partner"],
        direction=ConversationMessage.Direction.MASTER_TO_CLIENT,
    )
    assert message.client_profile == context["client"]
    assert message.text == "I will contact you here."

    context["client"].bot_unreachable = True
    context["client"].save(update_fields=["bot_unreachable", "updated_at"])
    unavailable = _failed_notification_result_from_callback_data(
        partner=context["partner"],
        actor=context["owner"],
        data=f"nf:message:{source_id.hex}",
    )
    assert unavailable.wait_for_text is False
    assert "недоступний" in unavailable.reply.text


@pytest.mark.django_db
def test_failed_reminder_markup_accepts_uuid_without_database_lookup():
    source_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

    markup = failed_delivery_to_master_markup(
        source_notification_id=source_id,
        language="en",
    )

    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert callbacks == [
        "nf:retry:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "nf:message:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "nf:client:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    ]
