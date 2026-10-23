from datetime import time, timedelta

import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.appointments.models import Appointment, AppointmentProposal
from apps.appointments.proposals import (
    AppointmentProposalOptionInput,
    accept_appointment_proposal_option_as_partner,
    create_appointment_proposal,
)
from apps.appointments.services import (
    AppointmentServiceItemInput,
    cancel_appointment_by_client,
    confirm_requested_appointment,
    create_confirmed_appointment,
    create_manual_appointment,
    create_requested_appointment,
    transition_appointment_status,
)
from apps.bot.models import BotInstance, PartnerTelegramContact, PartnerTelegramContactLink
from apps.bot.selectors import primary_master_telegram_contact
from apps.bot.services import (
    bind_master_contact_from_start_payload,
    create_master_contact_link,
    handle_start_command,
    render_invalid_master_contact_link_message,
    set_bot_token,
)
from apps.broadcasts.models import BroadcastRecipient
from apps.broadcasts.services import create_broadcast_campaign, queue_broadcast_campaign
from apps.broadcasts.tasks import dispatch_due_recipients_task
from apps.catalog.services import create_service, create_service_category
from apps.clients.services import (
    resolve_client_profile_for_telegram_identity,
    upsert_telegram_identity,
)
from apps.finance.services import (
    create_late_arrival_penalty_for_appointment,
    mark_penalty_paid,
    update_penalty_settings,
    waive_penalty,
)
from apps.messaging.models import ConversationMessage
from apps.messaging.services import create_client_message_to_master, create_master_reply_to_client
from apps.messaging.tasks import dispatch_queued_conversation_messages_task
from apps.notifications.models import PartnerNotificationSettings, ScheduledNotification
from apps.notifications.tasks import (
    dispatch_due_notifications_task,
    schedule_morning_digests_task,
)
from apps.partners.models import Partner
from apps.partners.services import create_partner_with_owner
from apps.schedule.selectors import default_booking_resource_for_partner
from core.timezones import get_zone

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
def test_start_command_without_admin_payload_resolves_partner_client_profile(
    partner_with_bot_username,
):
    created = partner_with_bot_username

    result = handle_start_command(
        partner=created.partner,
        telegram_user_id=555006,
        username="client",
        first_name="Client",
        language_code="ru",
    )

    assert result.master_contact_bound is False
    assert result.client_profile_id
    assert result.text == f"Добро пожаловать в бот {created.partner.name}."


@pytest.mark.django_db
def test_start_command_with_admin_payload_binds_master_contact(partner_with_bot_username):
    created = partner_with_bot_username
    link = create_master_contact_link(actor=created.owner, partner=created.partner)

    result = handle_start_command(
        partner=created.partner,
        telegram_user_id=555007,
        username="master",
        start_payload=link.start_payload,
    )

    assert result.master_contact_bound is True
    assert result.master_contact_id
    assert "підключено" in result.text


@pytest.mark.django_db
def test_invalid_master_contact_link_message_is_system_default(partner_with_bot_username):
    created = partner_with_bot_username

    text = render_invalid_master_contact_link_message(partner=created.partner)

    assert "недійсне" in text


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


@pytest.mark.django_db
def test_manual_appointment_notice_dispatches_to_linked_client(
    bot_token_settings,
    partner_with_bot_username,
    monkeypatch,
):
    created = partner_with_bot_username
    set_bot_token(
        actor=created.owner,
        partner=created.partner,
        raw_token=VALID_TOKEN,
    )
    PartnerNotificationSettings.objects.filter(partner=created.partner).update(
        quiet_hours_enabled=False,
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
    client_identity = upsert_telegram_identity(
        telegram_user_id=555050,
        username="manual-booking-client",
    )
    client_profile = resolve_client_profile_for_telegram_identity(
        partner=created.partner,
        telegram_identity=client_identity,
    )
    appointment = create_manual_appointment(
        actor=created.owner,
        partner=created.partner,
        booking_resource=default_booking_resource_for_partner(created.partner),
        client_profile=client_profile,
        start_at=timezone.now() + timedelta(days=2),
        service_items=[AppointmentServiceItemInput(service=service)],
    )
    sent_payloads = []

    async def fake_send(token, chat_id, text, photo="", reply_markup=None):
        sent_payloads.append({"token": token, "chat_id": chat_id, "text": text})

    monkeypatch.setattr("apps.bot.delivery._send_telegram_payload", fake_send)

    processed = dispatch_due_notifications_task()

    notice = ScheduledNotification.objects.get(
        appointment=appointment,
        type=ScheduledNotification.Type.CLIENT_APPOINTMENT_CREATED,
    )
    assert processed == 1
    assert notice.status == ScheduledNotification.Status.SENT
    assert sent_payloads[0]["token"] == VALID_TOKEN
    assert sent_payloads[0]["chat_id"] == 555050
    assert "Запис до Pilot Partner підтверджено" in sent_payloads[0]["text"]


@pytest.mark.django_db
def test_booking_request_dispatches_actionable_notice_to_primary_master(
    bot_token_settings,
    partner_with_bot_username,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    created = partner_with_bot_username
    set_bot_token(
        actor=created.owner,
        partner=created.partner,
        raw_token=VALID_TOKEN,
    )
    PartnerNotificationSettings.objects.filter(partner=created.partner).update(
        quiet_hours_enabled=False,
    )
    master_identity = upsert_telegram_identity(
        telegram_user_id=555055,
        username="request-master",
    )
    PartnerTelegramContact.objects.create(
        partner=created.partner,
        telegram_identity=master_identity,
        user=created.owner,
        is_primary=True,
    )
    client_identity = upsert_telegram_identity(
        telegram_user_id=555056,
        username="request-client",
        first_name="Ira",
    )
    client_profile = resolve_client_profile_for_telegram_identity(
        partner=created.partner,
        telegram_identity=client_identity,
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
    with django_capture_on_commit_callbacks(execute=True):
        appointment = create_requested_appointment(
            actor=None,
            partner=created.partner,
            booking_resource=default_booking_resource_for_partner(created.partner),
            client_profile=client_profile,
            start_at=timezone.now() + timedelta(days=2),
            service_items=[AppointmentServiceItemInput(service=service)],
        )
    sent_payloads = []

    async def fake_send(token, chat_id, text, photo="", reply_markup=None):
        sent_payloads.append(
            {
                "token": token,
                "chat_id": chat_id,
                "text": text,
                "reply_markup": reply_markup,
            }
        )

    monkeypatch.setattr("apps.bot.delivery._send_telegram_payload", fake_send)

    processed = dispatch_due_notifications_task()

    notice = ScheduledNotification.objects.get(
        appointment=appointment,
        type=ScheduledNotification.Type.MASTER_BOOKING_REQUEST,
    )
    callbacks = [
        button.callback_data
        for row in sent_payloads[0]["reply_markup"].inline_keyboard
        for button in row
    ]
    assert processed == 1
    assert notice.status == ScheduledNotification.Status.SENT
    assert sent_payloads[0]["token"] == VALID_TOKEN
    assert sent_payloads[0]["chat_id"] == 555055
    assert "Нова заявка" in sent_payloads[0]["text"]
    assert callbacks == [
        f"mr:confirm:{appointment.id.hex}",
        f"mr:decline:{appointment.id.hex}",
        f"mr:propose:{appointment.id.hex}",
    ]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("result", "notification_type", "expected_text"),
    [
        (
            Appointment.Status.CONFIRMED,
            ScheduledNotification.Type.CLIENT_BOOKING_REQUEST_CONFIRMED,
            "підтвердив запис",
        ),
        (
            Appointment.Status.CANCELLED_BY_MASTER,
            ScheduledNotification.Type.CLIENT_BOOKING_REQUEST_DECLINED,
            "відхилив заявку",
        ),
    ],
)
def test_booking_request_resolution_dispatches_to_client(
    bot_token_settings,
    partner_with_bot_username,
    monkeypatch,
    django_capture_on_commit_callbacks,
    result,
    notification_type,
    expected_text,
):
    created = partner_with_bot_username
    set_bot_token(
        actor=created.owner,
        partner=created.partner,
        raw_token=VALID_TOKEN,
    )
    PartnerNotificationSettings.objects.filter(partner=created.partner).update(
        quiet_hours_enabled=False,
    )
    client_identity = upsert_telegram_identity(
        telegram_user_id=555057,
        username="request-resolution-client",
        first_name="Ira",
    )
    client_profile = resolve_client_profile_for_telegram_identity(
        partner=created.partner,
        telegram_identity=client_identity,
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
    with django_capture_on_commit_callbacks(execute=True):
        appointment = create_requested_appointment(
            actor=None,
            partner=created.partner,
            booking_resource=default_booking_resource_for_partner(created.partner),
            client_profile=client_profile,
            start_at=timezone.now() + timedelta(days=2),
            service_items=[AppointmentServiceItemInput(service=service)],
        )
    with django_capture_on_commit_callbacks(execute=True):
        if result == Appointment.Status.CONFIRMED:
            confirm_requested_appointment(actor=created.owner, appointment=appointment)
        else:
            transition_appointment_status(
                actor=created.owner,
                appointment=appointment,
                new_status=result,
            )
    sent_payloads = []

    async def fake_send(token, chat_id, text, photo="", reply_markup=None):
        sent_payloads.append({"token": token, "chat_id": chat_id, "text": text})

    monkeypatch.setattr("apps.bot.delivery._send_telegram_payload", fake_send)

    processed = dispatch_due_notifications_task()

    notice = ScheduledNotification.objects.get(
        appointment=appointment,
        type=notification_type,
    )
    assert processed == 1
    assert notice.status == ScheduledNotification.Status.SENT
    assert sent_payloads[0]["chat_id"] == 555057
    assert expected_text in sent_payloads[0]["text"]


@pytest.mark.django_db
def test_auto_confirmed_client_booking_dispatches_to_primary_master(
    bot_token_settings,
    partner_with_bot_username,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    created = partner_with_bot_username
    set_bot_token(
        actor=created.owner,
        partner=created.partner,
        raw_token=VALID_TOKEN,
    )
    PartnerNotificationSettings.objects.filter(partner=created.partner).update(
        quiet_hours_enabled=False,
    )
    master_identity = upsert_telegram_identity(
        telegram_user_id=555058,
        username="confirmed-booking-master",
    )
    PartnerTelegramContact.objects.create(
        partner=created.partner,
        telegram_identity=master_identity,
        user=created.owner,
        is_primary=True,
    )
    client_identity = upsert_telegram_identity(
        telegram_user_id=555059,
        username="confirmed-booking-client",
        first_name="Ira",
    )
    client_profile = resolve_client_profile_for_telegram_identity(
        partner=created.partner,
        telegram_identity=client_identity,
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
    start_at = timezone.now() + timedelta(days=2)
    with django_capture_on_commit_callbacks(execute=True):
        appointment = create_confirmed_appointment(
            actor=None,
            partner=created.partner,
            booking_resource=default_booking_resource_for_partner(created.partner),
            client_profile=client_profile,
            start_at=start_at,
            service_items=[AppointmentServiceItemInput(service=service)],
            client_comment="Please use a neutral color.",
        )
    sent_payloads = []

    async def fake_send(token, chat_id, text, photo="", reply_markup=None):
        sent_payloads.append({"token": token, "chat_id": chat_id, "text": text})

    monkeypatch.setattr("apps.bot.delivery._send_telegram_payload", fake_send)

    processed = dispatch_due_notifications_task()

    notice = ScheduledNotification.objects.get(
        appointment=appointment,
        type=ScheduledNotification.Type.MASTER_CLIENT_BOOKED_APPOINTMENT,
    )
    assert processed == 1
    assert notice.status == ScheduledNotification.Status.SENT
    assert sent_payloads[0]["token"] == VALID_TOKEN
    assert sent_payloads[0]["chat_id"] == 555058
    assert "Новий підтверджений запис: Ira" in sent_payloads[0]["text"]
    assert "Manicure" in sent_payloads[0]["text"]
    assert "Коментар: Please use a neutral color." in sent_payloads[0]["text"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("status", "notification_type", "expected_text"),
    [
        (
            Appointment.Status.CANCELLED_BY_MASTER,
            ScheduledNotification.Type.CLIENT_APPOINTMENT_CANCELLED_BY_MASTER,
            "скасував запис",
        ),
        (
            Appointment.Status.COMPLETED,
            ScheduledNotification.Type.CLIENT_APPOINTMENT_COMPLETED,
            "Візит до Pilot Partner завершено",
        ),
        (
            Appointment.Status.NO_SHOW,
            ScheduledNotification.Type.CLIENT_APPOINTMENT_NO_SHOW,
            "позначив запис як неявку",
        ),
    ],
)
def test_master_final_status_dispatches_to_client(
    bot_token_settings,
    partner_with_bot_username,
    monkeypatch,
    django_capture_on_commit_callbacks,
    status,
    notification_type,
    expected_text,
):
    created = partner_with_bot_username
    set_bot_token(
        actor=created.owner,
        partner=created.partner,
        raw_token=VALID_TOKEN,
    )
    PartnerNotificationSettings.objects.filter(partner=created.partner).update(
        quiet_hours_enabled=False,
    )
    client_identity = upsert_telegram_identity(
        telegram_user_id=555062,
        username="final-status-client",
        first_name="Ira",
    )
    client_profile = resolve_client_profile_for_telegram_identity(
        partner=created.partner,
        telegram_identity=client_identity,
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
    with django_capture_on_commit_callbacks(execute=True):
        appointment = create_confirmed_appointment(
            actor=created.owner,
            partner=created.partner,
            booking_resource=default_booking_resource_for_partner(created.partner),
            client_profile=client_profile,
            start_at=timezone.now() - timedelta(hours=2),
            service_items=[AppointmentServiceItemInput(service=service)],
        )
    with django_capture_on_commit_callbacks(execute=True):
        transition_appointment_status(
            actor=created.owner,
            appointment=appointment,
            new_status=status,
            reason="Master is unavailable.",
        )
    sent_payloads = []

    async def fake_send(token, chat_id, text, photo="", reply_markup=None):
        sent_payloads.append({"token": token, "chat_id": chat_id, "text": text})

    monkeypatch.setattr("apps.bot.delivery._send_telegram_payload", fake_send)

    processed = dispatch_due_notifications_task()

    notice = ScheduledNotification.objects.get(
        appointment=appointment,
        type=notification_type,
    )
    assert processed == 1
    assert notice.status == ScheduledNotification.Status.SENT
    assert sent_payloads[0]["chat_id"] == 555062
    assert expected_text in sent_payloads[0]["text"]
    if status == Appointment.Status.CANCELLED_BY_MASTER:
        assert "Master is unavailable." in sent_payloads[0]["text"]


@pytest.mark.django_db
def test_penalty_lifecycle_dispatches_localized_client_notifications(
    bot_token_settings,
    partner_with_bot_username,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    created = partner_with_bot_username
    set_bot_token(
        actor=created.owner,
        partner=created.partner,
        raw_token=VALID_TOKEN,
    )
    PartnerNotificationSettings.objects.filter(partner=created.partner).update(
        quiet_hours_enabled=False,
    )
    update_penalty_settings(
        actor=created.owner,
        partner=created.partner,
        penalties_enabled=True,
    )
    client_identity = upsert_telegram_identity(
        telegram_user_id=555063,
        username="penalty-client",
        first_name="Ira",
    )
    client_profile = resolve_client_profile_for_telegram_identity(
        partner=created.partner,
        telegram_identity=client_identity,
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
    appointment = create_confirmed_appointment(
        actor=created.owner,
        partner=created.partner,
        booking_resource=default_booking_resource_for_partner(created.partner),
        client_profile=client_profile,
        start_at=timezone.now() - timedelta(days=3),
        service_items=[AppointmentServiceItemInput(service=service)],
    )
    waived_appointment = create_confirmed_appointment(
        actor=created.owner,
        partner=created.partner,
        booking_resource=default_booking_resource_for_partner(created.partner),
        client_profile=client_profile,
        start_at=timezone.now() - timedelta(days=2),
        service_items=[AppointmentServiceItemInput(service=service)],
    )
    sent_payloads = []

    async def fake_send(token, chat_id, text, photo="", reply_markup=None):
        sent_payloads.append({"token": token, "chat_id": chat_id, "text": text})

    monkeypatch.setattr("apps.bot.delivery._send_telegram_payload", fake_send)

    with django_capture_on_commit_callbacks(execute=True):
        paid_penalty = create_late_arrival_penalty_for_appointment(
            actor=created.owner,
            appointment=appointment,
            late_by_minutes=15,
        )
    assert dispatch_due_notifications_task() == 1

    with django_capture_on_commit_callbacks(execute=True):
        mark_penalty_paid(actor=created.owner, penalty=paid_penalty)
    assert dispatch_due_notifications_task() == 1

    with django_capture_on_commit_callbacks(execute=True):
        waived_penalty = create_late_arrival_penalty_for_appointment(
            actor=created.owner,
            appointment=waived_appointment,
            late_by_minutes=20,
        )
    assert dispatch_due_notifications_task() == 1

    with django_capture_on_commit_callbacks(execute=True):
        waive_penalty(actor=created.owner, penalty=waived_penalty)
    assert dispatch_due_notifications_task() == 1

    assert [payload["chat_id"] for payload in sent_payloads] == [555063] * 4
    assert "запізнення на 15 хв: 20.00 UAH" in sent_payloads[0]["text"]
    assert sent_payloads[1]["text"] == "Штраф оплачено: 20.00 UAH."
    assert "запізнення на 20 хв: 20.00 UAH" in sent_payloads[2]["text"]
    assert sent_payloads[3]["text"] == "Штраф списано: 20.00 UAH."


@pytest.mark.django_db
def test_client_cancellation_notice_dispatches_to_primary_master(
    bot_token_settings,
    partner_with_bot_username,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    created = partner_with_bot_username
    set_bot_token(
        actor=created.owner,
        partner=created.partner,
        raw_token=VALID_TOKEN,
    )
    PartnerNotificationSettings.objects.filter(partner=created.partner).update(
        quiet_hours_enabled=False,
    )
    master_identity = upsert_telegram_identity(
        telegram_user_id=555060,
        username="cancellation-master",
    )
    PartnerTelegramContact.objects.create(
        partner=created.partner,
        telegram_identity=master_identity,
        user=created.owner,
        is_primary=True,
    )
    client_identity = upsert_telegram_identity(
        telegram_user_id=555061,
        username="cancellation-client",
        first_name="Ira",
    )
    client_profile = resolve_client_profile_for_telegram_identity(
        partner=created.partner,
        telegram_identity=client_identity,
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
    start_at = timezone.now() + timedelta(days=2)
    appointment = create_confirmed_appointment(
        actor=created.owner,
        partner=created.partner,
        booking_resource=default_booking_resource_for_partner(created.partner),
        client_profile=client_profile,
        start_at=start_at,
        service_items=[AppointmentServiceItemInput(service=service)],
    )
    sent_payloads = []

    async def fake_send(token, chat_id, text, photo="", reply_markup=None):
        sent_payloads.append({"token": token, "chat_id": chat_id, "text": text})

    monkeypatch.setattr("apps.bot.delivery._send_telegram_payload", fake_send)
    with django_capture_on_commit_callbacks(execute=True):
        cancel_appointment_by_client(
            client_profile=client_profile,
            appointment=appointment,
            now=start_at - timedelta(hours=7),
        )

    processed = dispatch_due_notifications_task()

    notice = ScheduledNotification.objects.get(
        appointment=appointment,
        type=ScheduledNotification.Type.MASTER_CLIENT_CANCELLED_APPOINTMENT,
    )
    assert processed == 1
    assert notice.status == ScheduledNotification.Status.SENT
    assert sent_payloads == [
        {
            "token": VALID_TOKEN,
            "chat_id": 555060,
            "text": "Клієнт скасував запис: Ira, "
            f"{start_at.astimezone(get_zone(created.partner.timezone)).date().isoformat()} о "
            f"{start_at.astimezone(get_zone(created.partner.timezone)).strftime('%H:%M')}, "
            "Manicure.",
        }
    ]


@pytest.mark.django_db
def test_appointment_proposal_notice_dispatches_with_client_action(
    bot_token_settings,
    partner_with_bot_username,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    created = partner_with_bot_username
    set_bot_token(
        actor=created.owner,
        partner=created.partner,
        raw_token=VALID_TOKEN,
    )
    PartnerNotificationSettings.objects.filter(partner=created.partner).update(
        quiet_hours_enabled=False,
    )
    identity = upsert_telegram_identity(
        telegram_user_id=555070,
        username="proposal-client",
        first_name="Ira",
    )
    client_profile = resolve_client_profile_for_telegram_identity(
        partner=created.partner,
        telegram_identity=identity,
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
    appointment = create_confirmed_appointment(
        actor=created.owner,
        partner=created.partner,
        booking_resource=default_booking_resource_for_partner(created.partner),
        client_profile=client_profile,
        start_at=timezone.now() + timedelta(days=2),
        service_items=[AppointmentServiceItemInput(service=service)],
    )
    with django_capture_on_commit_callbacks(execute=True):
        proposal = create_appointment_proposal(
            actor=created.owner,
            appointment=appointment,
            proposal_type=AppointmentProposal.Type.RESCHEDULE_CONFIRMED,
            options=[
                AppointmentProposalOptionInput(start_at=appointment.start_at + timedelta(days=1))
            ],
            message="Please choose this alternative.",
        )
    sent_payloads = []

    async def fake_send(token, chat_id, text, photo="", reply_markup=None):
        sent_payloads.append(
            {
                "token": token,
                "chat_id": chat_id,
                "text": text,
                "reply_markup": reply_markup,
            }
        )

    monkeypatch.setattr("apps.bot.delivery._send_telegram_payload", fake_send)

    processed = dispatch_due_notifications_task()

    notice = ScheduledNotification.objects.get(
        appointment_proposal=proposal,
        type=ScheduledNotification.Type.CLIENT_APPOINTMENT_PROPOSAL,
    )
    assert processed == 1
    assert notice.status == ScheduledNotification.Status.SENT
    assert sent_payloads[0]["token"] == VALID_TOKEN
    assert sent_payloads[0]["chat_id"] == 555070
    assert "Please choose this alternative." in sent_payloads[0]["text"]
    assert (
        sent_payloads[0]["reply_markup"].inline_keyboard[0][0].callback_data
        == f"pf:proposal:{proposal.id.hex}:future:1"
    )


@pytest.mark.django_db
def test_client_reschedule_request_and_acceptance_dispatch_to_each_side(
    bot_token_settings,
    partner_with_bot_username,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    created = partner_with_bot_username
    set_bot_token(
        actor=created.owner,
        partner=created.partner,
        raw_token=VALID_TOKEN,
    )
    PartnerNotificationSettings.objects.filter(partner=created.partner).update(
        quiet_hours_enabled=False,
    )
    master_identity = upsert_telegram_identity(
        telegram_user_id=555080,
        username="reschedule-master",
    )
    PartnerTelegramContact.objects.create(
        partner=created.partner,
        telegram_identity=master_identity,
        user=created.owner,
        is_primary=True,
    )
    client_identity = upsert_telegram_identity(
        telegram_user_id=555081,
        username="reschedule-client",
        first_name="Ira",
    )
    client_profile = resolve_client_profile_for_telegram_identity(
        partner=created.partner,
        telegram_identity=client_identity,
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
    appointment = create_confirmed_appointment(
        actor=created.owner,
        partner=created.partner,
        booking_resource=default_booking_resource_for_partner(created.partner),
        client_profile=client_profile,
        start_at=timezone.now() + timedelta(days=2),
        service_items=[AppointmentServiceItemInput(service=service)],
    )
    proposed_start = appointment.start_at + timedelta(days=1)
    with django_capture_on_commit_callbacks(execute=True):
        proposal = create_appointment_proposal(
            actor=None,
            appointment=appointment,
            proposal_type=AppointmentProposal.Type.RESCHEDULE_CONFIRMED,
            options=[AppointmentProposalOptionInput(start_at=proposed_start)],
        )
    sent_payloads = []

    async def fake_send(token, chat_id, text, photo="", reply_markup=None):
        sent_payloads.append({"token": token, "chat_id": chat_id, "text": text})

    monkeypatch.setattr("apps.bot.delivery._send_telegram_payload", fake_send)

    assert dispatch_due_notifications_task() == 1
    local_start = proposed_start.astimezone(get_zone(created.partner.timezone))
    assert sent_payloads == [
        {
            "token": VALID_TOKEN,
            "chat_id": 555080,
            "text": "Клієнт просить перенести запис: Ira, "
            f"{local_start.date().isoformat()} о {local_start.strftime('%H:%M')}, Manicure.",
        }
    ]

    sent_payloads.clear()
    with django_capture_on_commit_callbacks(execute=True):
        accept_appointment_proposal_option_as_partner(
            actor=created.owner,
            option=proposal.options.get(),
        )

    assert dispatch_due_notifications_task() == 1
    response_notice = ScheduledNotification.objects.get(
        appointment_proposal=proposal,
        type=ScheduledNotification.Type.CLIENT_APPOINTMENT_PROPOSAL_ACCEPTED,
    )
    assert response_notice.status == ScheduledNotification.Status.SENT
    assert sent_payloads == [
        {
            "token": VALID_TOKEN,
            "chat_id": 555081,
            "text": "Новий час підтверджено: "
            f"{local_start.date().isoformat()} о {local_start.strftime('%H:%M')}, Manicure.",
        }
    ]


@pytest.mark.django_db
def test_morning_digest_task_schedules_one_digest_per_active_partner_local_day(
    partner_with_bot_username,
):
    created = partner_with_bot_username
    created.partner.status = Partner.Status.ACTIVE
    created.partner.save(update_fields=["status", "updated_at"])
    PartnerNotificationSettings.objects.filter(partner=created.partner).update(
        morning_digest_time_local=time(9, 30),
    )
    superuser = User.objects.create_superuser(username="digest-root", password="secret")
    inactive = create_partner_with_owner(
        actor=superuser,
        partner_name="Inactive Digest Partner",
        owner_username="inactive-digest-owner",
        owner_password="secret",
    )

    first_count = schedule_morning_digests_task()
    second_count = schedule_morning_digests_task()

    notification = ScheduledNotification.objects.get(
        partner=created.partner,
        type=ScheduledNotification.Type.MASTER_MORNING_DIGEST,
    )
    partner_zone = get_zone(created.partner.timezone)
    assert first_count == 1
    assert second_count == 1
    assert notification.due_at.astimezone(partner_zone).time() == time(9, 30)
    assert (
        ScheduledNotification.objects.filter(
            partner=inactive.partner,
            type=ScheduledNotification.Type.MASTER_MORNING_DIGEST,
        ).count()
        == 0
    )
    assert (
        ScheduledNotification.objects.filter(
            partner=created.partner,
            type=ScheduledNotification.Type.MASTER_MORNING_DIGEST,
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_conversation_task_delivers_client_message_to_primary_master_contact(
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
    master_identity = upsert_telegram_identity(
        telegram_user_id=555101,
        username="master",
    )
    contact = PartnerTelegramContact.objects.create(
        partner=created.partner,
        telegram_identity=master_identity,
        user=created.owner,
        is_primary=True,
        last_delivery_failure_code="bot_blocked",
        last_delivery_failure_at=timezone.now(),
    )
    client_identity = upsert_telegram_identity(
        telegram_user_id=555102,
        username="client",
    )
    client = resolve_client_profile_for_telegram_identity(
        partner=created.partner,
        telegram_identity=client_identity,
    )
    create_client_message_to_master(
        partner=created.partner,
        client_profile=client,
        text="Need help with my booking",
    )
    sent_payloads = []

    async def fake_send(token, chat_id, text, photo="", reply_markup=None):
        sent_payloads.append(
            {
                "token": token,
                "chat_id": chat_id,
                "text": text,
                "callback_data": reply_markup.inline_keyboard[0][0].callback_data,
            }
        )

    monkeypatch.setattr("apps.bot.delivery._send_telegram_payload", fake_send)

    processed = dispatch_queued_conversation_messages_task()

    message = ConversationMessage.objects.get(partner=created.partner)
    contact.refresh_from_db()
    assert processed == 1
    assert message.delivery_status == ConversationMessage.DeliveryStatus.SENT
    assert contact.last_delivery_failure_code == ""
    assert contact.last_delivery_failure_at is None
    assert sent_payloads == [
        {
            "token": VALID_TOKEN,
            "chat_id": 555101,
            "text": "Повідомлення від client : Need help with my booking",
            "callback_data": f"mm:reply:{message.id.hex}",
        }
    ]


@pytest.mark.django_db
def test_conversation_task_delivers_master_reply_to_linked_client(
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
    client_identity = upsert_telegram_identity(
        telegram_user_id=555103,
        username="client",
    )
    client = resolve_client_profile_for_telegram_identity(
        partner=created.partner,
        telegram_identity=client_identity,
    )
    create_master_reply_to_client(
        actor=created.owner,
        client_profile=client,
        text="Sure, I will check it.",
    )
    sent_payloads = []

    async def fake_send(token, chat_id, text, photo="", reply_markup=None):
        sent_payloads.append({"token": token, "chat_id": chat_id, "text": text})

    monkeypatch.setattr("apps.bot.delivery._send_telegram_payload", fake_send)

    processed = dispatch_queued_conversation_messages_task()

    message = ConversationMessage.objects.get(partner=created.partner)
    assert processed == 1
    assert message.delivery_status == ConversationMessage.DeliveryStatus.SENT
    assert sent_payloads == [
        {
            "token": VALID_TOKEN,
            "chat_id": 555103,
            "text": "Pilot Partner відповідає: Sure, I will check it.",
        }
    ]
