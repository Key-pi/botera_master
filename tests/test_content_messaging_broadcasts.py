from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.bot.models import PartnerTemplateOverride, SystemTemplate
from apps.bot.services import (
    render_content_template,
    reset_partner_template_override,
    seed_system_templates,
    set_partner_template_override,
)
from apps.broadcasts.models import BroadcastCampaign, BroadcastRecipient
from apps.broadcasts.services import (
    BroadcastDeliveryResult,
    create_broadcast_campaign,
    dispatch_broadcast_recipient,
    queue_broadcast_campaign,
)
from apps.clients.services import (
    create_manual_client_profile,
    mark_client_unreachable,
    resolve_client_profile_for_telegram_identity,
    set_marketing_opt_out,
    upsert_telegram_identity,
)
from apps.messaging.models import ConversationMessage
from apps.messaging.services import (
    MAX_CONVERSATION_MESSAGE_LENGTH,
    create_master_reply_to_client,
    dispatch_conversation_message,
)
from apps.notifications.models import DeliveryAttempt
from apps.notifications.services import DeliveryResult
from apps.partners.services import create_partner_with_owner


@pytest.fixture
def content_context():
    superuser = User.objects.create_superuser(username="root", password="secret")
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Content Partner",
        owner_username="content-owner",
        owner_password="secret",
    )
    return created


@pytest.fixture
def communication_context(content_context):
    created = content_context
    active_identity = upsert_telegram_identity(telegram_user_id=1001, username="active")
    opted_identity = upsert_telegram_identity(telegram_user_id=1002, username="opted")
    blocked_identity = upsert_telegram_identity(telegram_user_id=1003, username="blocked")
    active_client = resolve_client_profile_for_telegram_identity(
        partner=created.partner,
        telegram_identity=active_identity,
    )
    opted_client = resolve_client_profile_for_telegram_identity(
        partner=created.partner,
        telegram_identity=opted_identity,
    )
    blocked_client = resolve_client_profile_for_telegram_identity(
        partner=created.partner,
        telegram_identity=blocked_identity,
    )
    manual_client = create_manual_client_profile(
        actor=created.owner,
        partner=created.partner,
        name="Manual",
        phone="+380501111111",
    )
    set_marketing_opt_out(client_profile=opted_client, opt_out=True)
    mark_client_unreachable(client_profile=blocked_client, failure_code="bot_blocked")
    return {
        "partner": created.partner,
        "owner": created.owner,
        "active_client": active_client,
        "opted_client": opted_client,
        "blocked_client": blocked_client,
        "manual_client": manual_client,
    }


@pytest.mark.django_db
def test_content_template_override_validation_and_language_fallback(content_context):
    created = content_context
    seed_system_templates()

    assert SystemTemplate.objects.filter(key="start.welcome", language="uk").exists()
    required_field_template = SystemTemplate.objects.get(
        key="error.required_field_missing",
        language="en",
    )
    final_confirm_template = SystemTemplate.objects.get(
        key="booking.final_confirm",
        language="en",
    )
    master_request_template = SystemTemplate.objects.get(
        key="master.booking_request",
        language="en",
    )
    master_action_template = SystemTemplate.objects.get(
        key="master.booking_request_confirmed",
        language="en",
    )
    assert required_field_template.editable is False
    assert required_field_template.placeholder_allowlist == ["missing_fields"]
    assert "comment" in final_confirm_template.placeholder_allowlist
    assert master_request_template.editable is True
    assert "fit_note" in master_request_template.placeholder_allowlist
    assert master_action_template.editable is False

    override = set_partner_template_override(
        actor=created.owner,
        partner=created.partner,
        key="start.welcome",
        language="uk",
        body="Привіт, {partner_name}!",
    )
    rendered_uk = render_content_template(
        partner=created.partner,
        key="start.welcome",
        language="uk",
        context={"partner_name": "Nails"},
    )
    rendered_en = render_content_template(
        partner=created.partner,
        key="start.welcome",
        language="en",
        context={"partner_name": "Nails"},
    )

    assert override.body == "Привіт, {partner_name}!"
    assert rendered_uk.body == "Привіт, Nails!"
    assert rendered_uk.source == "partner_override"
    assert rendered_en.body == "Welcome to Nails."
    assert rendered_en.source == "system_default"

    with pytest.raises(ValidationError):
        set_partner_template_override(
            actor=created.owner,
            partner=created.partner,
            key="start.welcome",
            language="uk",
            body="Bad {unknown}",
        )

    with pytest.raises(ValidationError):
        set_partner_template_override(
            actor=created.owner,
            partner=created.partner,
            key="booking.slot_taken",
            language="uk",
            body="custom",
        )

    reset_partner_template_override(
        actor=created.owner,
        partner=created.partner,
        key="start.welcome",
        language="uk",
    )

    assert not PartnerTemplateOverride.objects.filter(id=override.id).exists()


@pytest.mark.django_db
def test_master_reply_terminal_failure_marks_client_unreachable(communication_context):
    context = communication_context
    message = create_master_reply_to_client(
        actor=context["owner"],
        client_profile=context["active_client"],
        text="Please contact me before the visit.",
    )

    def deliver(_message):
        return DeliveryResult(
            success=False,
            failure_code="bot_blocked",
            failure_message="Forbidden: bot was blocked by the user",
            terminal=True,
        )

    status = dispatch_conversation_message(message=message, deliver=deliver)
    message.refresh_from_db()
    context["active_client"].refresh_from_db()

    assert status == ConversationMessage.DeliveryStatus.FAILED
    assert message.delivery_status == ConversationMessage.DeliveryStatus.FAILED
    assert context["active_client"].bot_unreachable is True
    assert DeliveryAttempt.objects.filter(conversation_message=message).count() == 1
    audit = AuditEvent.objects.get(target_id=str(message.id))
    assert audit.event_type == "messaging.message_created"
    assert message.text not in audit.message
    assert message.text not in str(audit.payload_masked)


@pytest.mark.django_db
def test_conversation_message_rejects_text_over_telegram_safe_limit(communication_context):
    context = communication_context

    with pytest.raises(ValidationError, match="must not exceed"):
        create_master_reply_to_client(
            actor=context["owner"],
            client_profile=context["active_client"],
            text="x" * (MAX_CONVERSATION_MESSAGE_LENGTH + 1),
        )

    assert ConversationMessage.objects.count() == 0


@pytest.mark.django_db
def test_broadcast_queue_excludes_opted_out_unreachable_and_manual_clients(
    communication_context,
):
    context = communication_context
    campaign = create_broadcast_campaign(
        actor=context["owner"],
        partner=context["partner"],
        text="Promo for this week",
        buttons=[{"type": "book", "label": "Book"}],
    )

    queued = queue_broadcast_campaign(actor=context["owner"], campaign=campaign)

    recipients = list(BroadcastRecipient.objects.filter(campaign=queued))
    assert queued.status == BroadcastCampaign.Status.QUEUED
    assert len(recipients) == 1
    assert recipients[0].client_profile == context["active_client"]


@pytest.mark.django_db
def test_broadcast_dispatch_success_and_skip_after_opt_out(communication_context):
    context = communication_context
    first_campaign = queue_broadcast_campaign(
        actor=context["owner"],
        campaign=create_broadcast_campaign(
            actor=context["owner"],
            partner=context["partner"],
            text="Today only",
        ),
        now=timezone.now() - timedelta(minutes=1),
    )
    first_recipient = BroadcastRecipient.objects.get(campaign=first_campaign)

    def deliver_success(_recipient):
        return BroadcastDeliveryResult(success=True)

    sent_status = dispatch_broadcast_recipient(
        recipient=first_recipient,
        deliver=deliver_success,
    )
    first_campaign.refresh_from_db()
    first_recipient.refresh_from_db()

    assert sent_status == BroadcastRecipient.Status.SENT
    assert first_recipient.status == BroadcastRecipient.Status.SENT
    assert first_campaign.status == BroadcastCampaign.Status.COMPLETED
    assert first_campaign.sent_count == 1

    second_campaign = queue_broadcast_campaign(
        actor=context["owner"],
        campaign=create_broadcast_campaign(
            actor=context["owner"],
            partner=context["partner"],
            text="Later message",
        ),
    )
    second_recipient = BroadcastRecipient.objects.get(campaign=second_campaign)
    set_marketing_opt_out(client_profile=context["active_client"], opt_out=True)

    skipped_status = dispatch_broadcast_recipient(
        recipient=second_recipient,
        deliver=deliver_success,
    )
    second_campaign.refresh_from_db()
    second_recipient.refresh_from_db()

    assert skipped_status == BroadcastRecipient.Status.SKIPPED
    assert second_recipient.status == BroadcastRecipient.Status.SKIPPED
    assert second_campaign.status == BroadcastCampaign.Status.COMPLETED
    assert second_campaign.skipped_count == 1
