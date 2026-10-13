from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.services import ensure_partner_access
from apps.broadcasts.models import (
    BROADCAST_RECIPIENT_RETAIN_DAYS,
    DEFAULT_BROADCAST_MAX_ATTEMPTS,
    BroadcastCampaign,
    BroadcastRecipient,
    BroadcastTemplate,
)
from apps.broadcasts.selectors import due_broadcast_recipients, eligible_broadcast_clients
from apps.clients.services import mark_client_unreachable
from apps.notifications.models import DeliveryAttempt
from apps.notifications.services import TERMINAL_TELEGRAM_FAILURE_CODES
from apps.partners.models import Partner
from core.security.masking import mask_sensitive_text

BROADCAST_RETRY_BACKOFFS = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15))
RATE_LIMIT_FAILURE_CODES = {"telegram_429", "too_many_requests", "rate_limited"}
ALLOWED_BROADCAST_BUTTON_TYPES = {"book", "my_appointments", "external_link"}


@dataclass(frozen=True, slots=True)
class BroadcastDeliveryResult:
    """Adapter-neutral result returned by a broadcast delivery backend."""

    success: bool
    failure_code: str = ""
    failure_message: str = ""
    terminal: bool = False
    skipped: bool = False
    retry_after_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class BroadcastDispatchSummary:
    sent: int = 0
    failed: int = 0
    queued_for_retry: int = 0
    skipped: int = 0
    ignored: int = 0

    def add(self, status: str) -> BroadcastDispatchSummary:
        if status == BroadcastRecipient.Status.SENT:
            return BroadcastDispatchSummary(
                sent=self.sent + 1,
                failed=self.failed,
                queued_for_retry=self.queued_for_retry,
                skipped=self.skipped,
                ignored=self.ignored,
            )
        if status == BroadcastRecipient.Status.FAILED:
            return BroadcastDispatchSummary(
                sent=self.sent,
                failed=self.failed + 1,
                queued_for_retry=self.queued_for_retry,
                skipped=self.skipped,
                ignored=self.ignored,
            )
        if status == BroadcastRecipient.Status.QUEUED:
            return BroadcastDispatchSummary(
                sent=self.sent,
                failed=self.failed,
                queued_for_retry=self.queued_for_retry + 1,
                skipped=self.skipped,
                ignored=self.ignored,
            )
        if status == BroadcastRecipient.Status.SKIPPED:
            return BroadcastDispatchSummary(
                sent=self.sent,
                failed=self.failed,
                queued_for_retry=self.queued_for_retry,
                skipped=self.skipped + 1,
                ignored=self.ignored,
            )
        return BroadcastDispatchSummary(
            sent=self.sent,
            failed=self.failed,
            queued_for_retry=self.queued_for_retry,
            skipped=self.skipped,
            ignored=self.ignored + 1,
        )


DeliverBroadcastRecipient = Callable[[BroadcastRecipient], BroadcastDeliveryResult]


def create_broadcast_template(
    *,
    actor: User,
    partner: Partner,
    name: str,
    text: str = "",
    photo: str = "",
    buttons: list[dict[str, Any]] | None = None,
) -> BroadcastTemplate:
    """Create a reusable partner-authored broadcast draft."""

    ensure_partner_access(actor, partner)
    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValidationError({"name": "Template name is required."})
    cleaned_text, cleaned_photo, cleaned_buttons = _clean_broadcast_content(
        text=text,
        photo=photo,
        buttons=buttons,
    )
    return BroadcastTemplate.objects.create(
        partner=partner,
        name=cleaned_name,
        text=cleaned_text,
        photo=cleaned_photo,
        buttons=cleaned_buttons,
        created_by=actor,
    )


def create_broadcast_campaign(
    *,
    actor: User,
    partner: Partner,
    text: str = "",
    photo: str = "",
    buttons: list[dict[str, Any]] | None = None,
    filters: dict | None = None,
    scheduled_at=None,
) -> BroadcastCampaign:
    """Create a draft or scheduled broadcast campaign."""

    ensure_partner_access(actor, partner)
    cleaned_text, cleaned_photo, cleaned_buttons = _clean_broadcast_content(
        text=text,
        photo=photo,
        buttons=buttons,
    )
    status = BroadcastCampaign.Status.SCHEDULED if scheduled_at else BroadcastCampaign.Status.DRAFT
    return BroadcastCampaign.objects.create(
        partner=partner,
        text=cleaned_text,
        photo=cleaned_photo,
        buttons=cleaned_buttons,
        filters=filters or {},
        status=status,
        scheduled_at=scheduled_at,
        created_by=actor,
    )


def queue_broadcast_campaign(
    *,
    actor: User,
    campaign: BroadcastCampaign,
    now=None,
) -> BroadcastCampaign:
    """Materialize eligible recipients and queue a broadcast for gradual sending."""

    ensure_partner_access(actor, campaign.partner)
    current_time = now or timezone.now()
    with transaction.atomic():
        locked = BroadcastCampaign.objects.select_for_update().get(id=campaign.id)
        if locked.status not in {
            BroadcastCampaign.Status.DRAFT,
            BroadcastCampaign.Status.SCHEDULED,
            BroadcastCampaign.Status.QUEUED,
        }:
            raise ValidationError({"status": "Campaign cannot be queued from this status."})

        clients = list(
            eligible_broadcast_clients(
                partner=locked.partner,
                filters=locked.filters,
                now=current_time,
            )
        )
        for client in clients:
            BroadcastRecipient.objects.get_or_create(
                partner=locked.partner,
                campaign=locked,
                client_profile=client,
                defaults={
                    "status": BroadcastRecipient.Status.QUEUED,
                    "next_attempt_at": current_time,
                    "max_attempts": DEFAULT_BROADCAST_MAX_ATTEMPTS,
                    "retain_until": current_time + timedelta(days=BROADCAST_RECIPIENT_RETAIN_DAYS),
                },
            )
        locked.started_at = locked.started_at or current_time
        locked.status = BroadcastCampaign.Status.QUEUED
        locked.save(update_fields=["status", "started_at", "updated_at"])
        _refresh_campaign_counters(locked)
        _finalize_campaign_if_done(locked)
    locked.refresh_from_db()
    return locked


def cancel_broadcast_campaign(*, actor: User, campaign: BroadcastCampaign) -> BroadcastCampaign:
    """Cancel a campaign and skip recipients that have not been sent yet."""

    ensure_partner_access(actor, campaign.partner)
    now = timezone.now()
    with transaction.atomic():
        locked = BroadcastCampaign.objects.select_for_update().get(id=campaign.id)
        locked.status = BroadcastCampaign.Status.CANCELLED
        locked.finished_at = now
        locked.save(update_fields=["status", "finished_at", "updated_at"])
        BroadcastRecipient.objects.filter(
            campaign=locked,
            status__in=[BroadcastRecipient.Status.QUEUED, BroadcastRecipient.Status.SENDING],
        ).update(status=BroadcastRecipient.Status.SKIPPED, updated_at=now)
        _refresh_campaign_counters(locked)
    locked.refresh_from_db()
    return locked


def dispatch_due_broadcast_recipients(
    *,
    deliver: DeliverBroadcastRecipient,
    now=None,
    limit: int = 100,
) -> BroadcastDispatchSummary:
    """Dispatch queued broadcast recipients with durable per-recipient state."""

    current_time = now or timezone.now()
    summary = BroadcastDispatchSummary()
    recipient_ids = list(
        due_broadcast_recipients(now=current_time).values_list("id", flat=True)[:limit]
    )
    for recipient_id in recipient_ids:
        recipient = BroadcastRecipient.objects.get(id=recipient_id)
        status = dispatch_broadcast_recipient(
            recipient=recipient,
            deliver=deliver,
            now=current_time,
        )
        summary = summary.add(status)
    return summary


def dispatch_broadcast_recipient(
    *,
    recipient: BroadcastRecipient,
    deliver: DeliverBroadcastRecipient,
    now=None,
) -> str:
    """Dispatch one recipient without holding locks during Telegram/network I/O."""

    current_time = now or timezone.now()
    prepared = _prepare_recipient_for_delivery(recipient=recipient, now=current_time)
    if prepared is None:
        return BroadcastRecipient.Status.SKIPPED

    result = deliver(prepared)
    return _record_broadcast_delivery_result(
        recipient=prepared,
        result=result,
        now=current_time,
    )


def _prepare_recipient_for_delivery(
    *,
    recipient: BroadcastRecipient,
    now,
) -> BroadcastRecipient | None:
    with transaction.atomic():
        locked = (
            BroadcastRecipient.objects.select_for_update()
            .select_related(
                "partner",
                "campaign",
                "client_profile",
                "client_profile__telegram_identity",
            )
            .get(id=recipient.id)
        )
        if locked.status != BroadcastRecipient.Status.QUEUED:
            return None
        if locked.next_attempt_at and locked.next_attempt_at > now:
            return None
        if _recipient_should_skip_before_delivery(locked):
            locked.status = BroadcastRecipient.Status.SKIPPED
            locked.save(update_fields=["status", "updated_at"])
            _record_broadcast_delivery_attempt(
                recipient=locked,
                status=DeliveryAttempt.Status.SKIPPED,
                attempted_at=now,
            )
            _refresh_campaign_counters(locked.campaign)
            _finalize_campaign_if_done(locked.campaign)
            return None
        locked.status = BroadcastRecipient.Status.SENDING
        locked.campaign.status = BroadcastCampaign.Status.SENDING
        locked.save(update_fields=["status", "updated_at"])
        locked.campaign.save(update_fields=["status", "updated_at"])
        return locked


def _record_broadcast_delivery_result(
    *,
    recipient: BroadcastRecipient,
    result: BroadcastDeliveryResult,
    now,
) -> str:
    with transaction.atomic():
        locked = (
            BroadcastRecipient.objects.select_for_update()
            .select_related("partner", "campaign", "client_profile")
            .get(id=recipient.id)
        )
        if locked.status != BroadcastRecipient.Status.SENDING:
            return locked.status

        if result.skipped:
            locked.status = BroadcastRecipient.Status.SKIPPED
            _record_broadcast_delivery_attempt(
                recipient=locked,
                status=DeliveryAttempt.Status.SKIPPED,
                attempted_at=now,
            )
        elif result.success:
            locked.status = BroadcastRecipient.Status.SENT
            locked.sent_at = now
            locked.failure_code = ""
            locked.failure_message_masked = ""
            _record_broadcast_delivery_attempt(
                recipient=locked,
                status=DeliveryAttempt.Status.SENT,
                attempted_at=now,
            )
        else:
            _apply_broadcast_delivery_failure(recipient=locked, result=result, now=now)

        locked.save(
            update_fields=[
                "status",
                "attempt_count",
                "next_attempt_at",
                "sent_at",
                "failure_code",
                "failure_message_masked",
                "updated_at",
            ]
        )
        _refresh_campaign_counters(locked.campaign)
        _finalize_campaign_if_done(locked.campaign)
        return locked.status


def _apply_broadcast_delivery_failure(
    *,
    recipient: BroadcastRecipient,
    result: BroadcastDeliveryResult,
    now,
) -> None:
    recipient.attempt_count += 1
    recipient.failure_code = result.failure_code[:128]
    recipient.failure_message_masked = mask_sensitive_text(result.failure_message)
    _record_broadcast_delivery_attempt(
        recipient=recipient,
        status=DeliveryAttempt.Status.FAILED,
        attempted_at=now,
        failure_code=recipient.failure_code,
        failure_message_masked=recipient.failure_message_masked,
    )

    terminal = result.terminal or result.failure_code in TERMINAL_TELEGRAM_FAILURE_CODES
    if terminal:
        recipient.status = BroadcastRecipient.Status.FAILED
        mark_client_unreachable(
            client_profile=recipient.client_profile,
            failure_code=recipient.failure_code or "terminal_delivery_failure",
        )
        return

    if _should_retry_broadcast_failure(recipient=recipient, result=result):
        recipient.status = BroadcastRecipient.Status.QUEUED
        recipient.next_attempt_at = now + _broadcast_retry_backoff(
            recipient=recipient,
            result=result,
        )
        return

    recipient.status = BroadcastRecipient.Status.FAILED


def _should_retry_broadcast_failure(
    *,
    recipient: BroadcastRecipient,
    result: BroadcastDeliveryResult,
) -> bool:
    if recipient.attempt_count >= recipient.max_attempts:
        return False
    return bool(
        result.retry_after_seconds
        or result.failure_code in RATE_LIMIT_FAILURE_CODES
        or not result.terminal
    )


def _broadcast_retry_backoff(
    *,
    recipient: BroadcastRecipient,
    result: BroadcastDeliveryResult,
) -> timedelta:
    if result.retry_after_seconds is not None:
        return timedelta(seconds=max(result.retry_after_seconds, 1))
    return BROADCAST_RETRY_BACKOFFS[
        min(recipient.attempt_count - 1, len(BROADCAST_RETRY_BACKOFFS) - 1)
    ]


def _recipient_should_skip_before_delivery(recipient: BroadcastRecipient) -> bool:
    if recipient.campaign.status == BroadcastCampaign.Status.CANCELLED:
        return True
    client = recipient.client_profile
    return (
        client.marketing_opt_out
        or client.bot_unreachable
        or not client.telegram_identity_id
    )


def _record_broadcast_delivery_attempt(
    *,
    recipient: BroadcastRecipient,
    status: str,
    attempted_at,
    failure_code: str = "",
    failure_message_masked: str = "",
) -> DeliveryAttempt:
    return DeliveryAttempt.objects.create(
        partner=recipient.partner,
        broadcast_recipient=recipient,
        status=status,
        failure_code=failure_code,
        failure_message_masked=failure_message_masked,
        message_preview_masked=mask_sensitive_text(recipient.campaign.text)[:500],
        attempted_at=attempted_at,
        retain_until=attempted_at + timedelta(days=BROADCAST_RECIPIENT_RETAIN_DAYS),
    )


def _refresh_campaign_counters(campaign: BroadcastCampaign) -> None:
    counts = {
        status: BroadcastRecipient.objects.filter(campaign=campaign, status=status).count()
        for status in BroadcastRecipient.Status.values
    }
    campaign.queued_count = counts.get(BroadcastRecipient.Status.QUEUED, 0) + counts.get(
        BroadcastRecipient.Status.SENDING,
        0,
    )
    campaign.sent_count = counts.get(BroadcastRecipient.Status.SENT, 0)
    campaign.failed_count = counts.get(BroadcastRecipient.Status.FAILED, 0)
    campaign.skipped_count = counts.get(BroadcastRecipient.Status.SKIPPED, 0)
    campaign.save(
        update_fields=[
            "queued_count",
            "sent_count",
            "failed_count",
            "skipped_count",
            "updated_at",
        ]
    )


def _finalize_campaign_if_done(campaign: BroadcastCampaign) -> None:
    campaign.refresh_from_db()
    if campaign.status == BroadcastCampaign.Status.CANCELLED:
        return
    has_pending = BroadcastRecipient.objects.filter(
        campaign=campaign,
        status__in=[BroadcastRecipient.Status.QUEUED, BroadcastRecipient.Status.SENDING],
    ).exists()
    if has_pending:
        return

    campaign.status = (
        BroadcastCampaign.Status.FAILED_PARTIAL
        if campaign.failed_count
        else BroadcastCampaign.Status.COMPLETED
    )
    campaign.finished_at = timezone.now()
    campaign.save(update_fields=["status", "finished_at", "updated_at"])


def _clean_broadcast_content(
    *,
    text: str,
    photo: str,
    buttons: list[dict[str, Any]] | None,
) -> tuple[str, str, list[dict[str, Any]]]:
    cleaned_text = text.strip()
    cleaned_photo = photo.strip()
    if not cleaned_text and not cleaned_photo:
        raise ValidationError({"text": "Broadcast needs text or photo."})
    cleaned_buttons = _clean_broadcast_buttons(buttons or [])
    return cleaned_text, cleaned_photo, cleaned_buttons


def _clean_broadcast_buttons(buttons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(buttons, list):
        raise ValidationError({"buttons": "Broadcast buttons must be a list."})
    cleaned: list[dict[str, Any]] = []
    for index, button in enumerate(buttons):
        if not isinstance(button, dict):
            raise ValidationError({"buttons": f"Button {index + 1} must be an object."})
        button_type = str(button.get("type", "")).strip()
        label = str(button.get("label", "")).strip()
        url = str(button.get("url", "")).strip()
        if button_type not in ALLOWED_BROADCAST_BUTTON_TYPES:
            raise ValidationError({"buttons": f"Unsupported button type: {button_type}."})
        if not label:
            raise ValidationError({"buttons": "Button label is required."})
        if len(label) > 64:
            raise ValidationError({"buttons": "Button label must be 64 characters or shorter."})
        if button_type == "external_link" and not url:
            raise ValidationError({"buttons": "External link button needs a URL."})
        cleaned_button = {"type": button_type, "label": label}
        if url:
            cleaned_button["url"] = url
        cleaned.append(cleaned_button)
    return cleaned
