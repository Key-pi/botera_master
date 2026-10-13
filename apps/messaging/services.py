from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.services import ensure_partner_access
from apps.clients.models import ClientProfile
from apps.clients.services import mark_client_unreachable
from apps.messaging.models import ConversationMessage
from apps.notifications.models import NOTIFICATION_RETAIN_DAYS, DeliveryAttempt
from apps.notifications.services import TERMINAL_TELEGRAM_FAILURE_CODES, DeliveryResult
from apps.partners.models import Partner
from core.security.masking import mask_sensitive_text

DeliverConversationMessage = Callable[[ConversationMessage], DeliveryResult]


def create_client_message_to_master(
    *,
    partner: Partner,
    client_profile: ClientProfile,
    text: str,
) -> ConversationMessage:
    """Store a client-originated message for later delivery to the master context."""

    _validate_partner_client(partner=partner, client_profile=client_profile)
    cleaned_text = text.strip()
    if not cleaned_text:
        raise ValidationError({"text": "Message text is required."})
    return ConversationMessage.objects.create(
        partner=partner,
        client_profile=client_profile,
        direction=ConversationMessage.Direction.CLIENT_TO_MASTER,
        text=cleaned_text,
    )


def create_master_reply_to_client(
    *,
    actor: User,
    client_profile: ClientProfile,
    text: str,
) -> ConversationMessage:
    """Store a master reply that should be delivered to the client through the bot."""

    ensure_partner_access(actor, client_profile.partner)
    cleaned_text = text.strip()
    if not cleaned_text:
        raise ValidationError({"text": "Message text is required."})
    return ConversationMessage.objects.create(
        partner=client_profile.partner,
        client_profile=client_profile,
        direction=ConversationMessage.Direction.MASTER_TO_CLIENT,
        text=cleaned_text,
        created_by=actor,
    )


def dispatch_conversation_message(
    *,
    message: ConversationMessage,
    deliver: DeliverConversationMessage,
) -> str:
    """Dispatch one queued conversation message without network I/O in a transaction."""

    prepared = _prepare_message_for_delivery(message=message)
    if prepared is None:
        return ConversationMessage.DeliveryStatus.SKIPPED

    result = deliver(prepared)
    return _record_message_delivery_result(message=prepared, result=result)


def _prepare_message_for_delivery(
    *,
    message: ConversationMessage,
) -> ConversationMessage | None:
    now = timezone.now()
    with transaction.atomic():
        locked = (
            ConversationMessage.objects.select_for_update()
            .select_related("partner", "client_profile", "client_profile__telegram_identity")
            .get(id=message.id)
        )
        if locked.delivery_status != ConversationMessage.DeliveryStatus.QUEUED:
            return None
        if _message_should_skip_delivery(locked):
            locked.delivery_status = ConversationMessage.DeliveryStatus.SKIPPED
            locked.save(update_fields=["delivery_status", "updated_at"])
            _record_message_delivery_attempt(
                message=locked,
                status=DeliveryAttempt.Status.SKIPPED,
                attempted_at=now,
            )
            return None
        locked.delivery_status = ConversationMessage.DeliveryStatus.SENDING
        locked.save(update_fields=["delivery_status", "updated_at"])
        return locked


def _record_message_delivery_result(
    *,
    message: ConversationMessage,
    result: DeliveryResult,
) -> str:
    now = timezone.now()
    with transaction.atomic():
        locked = (
            ConversationMessage.objects.select_for_update()
            .select_related("partner", "client_profile")
            .get(id=message.id)
        )
        if locked.delivery_status != ConversationMessage.DeliveryStatus.SENDING:
            return locked.delivery_status

        if result.skipped:
            locked.delivery_status = ConversationMessage.DeliveryStatus.SKIPPED
            _record_message_delivery_attempt(
                message=locked,
                status=DeliveryAttempt.Status.SKIPPED,
                attempted_at=now,
            )
        elif result.success:
            locked.delivery_status = ConversationMessage.DeliveryStatus.SENT
            locked.delivered_at = now
            locked.failure_code = ""
            locked.failure_message_masked = ""
            _record_message_delivery_attempt(
                message=locked,
                status=DeliveryAttempt.Status.SENT,
                attempted_at=now,
            )
        else:
            locked.delivery_status = ConversationMessage.DeliveryStatus.FAILED
            locked.failure_code = result.failure_code[:128]
            locked.failure_message_masked = mask_sensitive_text(result.failure_message)
            _record_message_delivery_attempt(
                message=locked,
                status=DeliveryAttempt.Status.FAILED,
                attempted_at=now,
                failure_code=locked.failure_code,
                failure_message_masked=locked.failure_message_masked,
            )
            if _should_mark_client_unreachable(message=locked, result=result):
                mark_client_unreachable(
                    client_profile=locked.client_profile,
                    failure_code=locked.failure_code or "terminal_delivery_failure",
                )

        locked.save(
            update_fields=[
                "delivery_status",
                "failure_code",
                "failure_message_masked",
                "delivered_at",
                "updated_at",
            ]
        )
        return locked.delivery_status


def _message_should_skip_delivery(message: ConversationMessage) -> bool:
    if message.direction != ConversationMessage.Direction.MASTER_TO_CLIENT:
        return False
    return (
        not message.client_profile.telegram_identity_id
        or message.client_profile.bot_unreachable
    )


def _should_mark_client_unreachable(
    *,
    message: ConversationMessage,
    result: DeliveryResult,
) -> bool:
    if message.direction != ConversationMessage.Direction.MASTER_TO_CLIENT:
        return False
    return result.terminal or result.failure_code in TERMINAL_TELEGRAM_FAILURE_CODES


def _record_message_delivery_attempt(
    *,
    message: ConversationMessage,
    status: str,
    attempted_at,
    failure_code: str = "",
    failure_message_masked: str = "",
) -> DeliveryAttempt:
    return DeliveryAttempt.objects.create(
        partner=message.partner,
        conversation_message=message,
        status=status,
        failure_code=failure_code,
        failure_message_masked=failure_message_masked,
        message_preview_masked=mask_sensitive_text(message.text)[:500],
        attempted_at=attempted_at,
        retain_until=attempted_at + timedelta(days=NOTIFICATION_RETAIN_DAYS),
    )


def _validate_partner_client(*, partner: Partner, client_profile: ClientProfile) -> None:
    if client_profile.partner_id != partner.id:
        raise ValidationError({"client_profile": "Client must belong to this partner."})
