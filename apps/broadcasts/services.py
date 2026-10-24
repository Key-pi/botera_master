from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.services import ensure_partner_access
from apps.audit.services import record_audit_event
from apps.broadcasts.models import (
    BROADCAST_RECIPIENT_RETAIN_DAYS,
    DEFAULT_BROADCAST_MAX_ATTEMPTS,
    BroadcastCampaign,
    BroadcastRecipient,
    BroadcastTemplate,
)
from apps.broadcasts.selectors import due_broadcast_recipients, eligible_broadcast_clients
from apps.catalog.models import Service
from apps.clients.services import mark_client_unreachable
from apps.notifications.models import DeliveryAttempt
from apps.notifications.services import TERMINAL_TELEGRAM_FAILURE_CODES
from apps.partners.models import Partner
from core.security.masking import mask_sensitive_text

BROADCAST_RETRY_BACKOFFS = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15))
RATE_LIMIT_FAILURE_CODES = {"telegram_429", "too_many_requests", "rate_limited"}
ALLOWED_BROADCAST_BUTTON_TYPES = {"book", "my_appointments", "external_link"}
ALLOWED_BROADCAST_FILTER_KEYS = {
    "service_id",
    "long_absent_days",
    "has_future_appointment",
    "without_future_appointment",
}
MAX_LONG_ABSENT_DAYS = 3650
TELEGRAM_MESSAGE_TEXT_LIMIT = 4096
TELEGRAM_PHOTO_CAPTION_LIMIT = 1024
validate_external_link_url = URLValidator(schemes=["http", "https"])


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
    cleaned_text, cleaned_photo, cleaned_buttons = validate_broadcast_content(
        text=text,
        photo=photo,
        buttons=buttons,
    )
    with transaction.atomic():
        template = BroadcastTemplate.objects.create(
            partner=partner,
            name=cleaned_name,
            text=cleaned_text,
            photo=cleaned_photo,
            buttons=cleaned_buttons,
            created_by=actor,
        )
        record_audit_event(
            partner=partner,
            actor=actor,
            event_type="broadcast.template_created",
            target=template,
            message="Broadcast template created.",
        )
    return template


def update_broadcast_template(
    *,
    actor: User,
    template: BroadcastTemplate,
    name: str,
    text: str = "",
    photo: str = "",
    buttons: list[dict[str, Any]] | None = None,
) -> BroadcastTemplate:
    """Update one reusable template through its trusted partner scope."""

    cleaned_name = name.strip()
    if not cleaned_name:
        raise ValidationError({"name": "Template name is required."})
    cleaned_text, cleaned_photo, cleaned_buttons = validate_broadcast_content(
        text=text,
        photo=photo,
        buttons=buttons,
    )
    with transaction.atomic():
        stored = (
            BroadcastTemplate.objects.select_for_update()
            .select_related("partner")
            .get(id=template.id)
        )
        ensure_partner_access(actor, stored.partner)
        stored.name = cleaned_name
        stored.text = cleaned_text
        stored.photo = cleaned_photo
        stored.buttons = cleaned_buttons
        stored.save(update_fields=["name", "text", "photo", "buttons", "updated_at"])
        record_audit_event(
            partner=stored.partner,
            actor=actor,
            event_type="broadcast.template_updated",
            target=stored,
            message="Broadcast template updated.",
        )
    return stored


def delete_broadcast_template(*, actor: User, template: BroadcastTemplate) -> None:
    """Delete one template while retaining a partner-visible audit event."""

    with transaction.atomic():
        stored = (
            BroadcastTemplate.objects.select_for_update()
            .select_related("partner")
            .get(id=template.id)
        )
        ensure_partner_access(actor, stored.partner)
        record_audit_event(
            partner=stored.partner,
            actor=actor,
            event_type="broadcast.template_deleted",
            target=stored,
            message="Broadcast template deleted.",
        )
        stored.delete()


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
    cleaned_text, cleaned_photo, cleaned_buttons = validate_broadcast_content(
        text=text,
        photo=photo,
        buttons=buttons,
    )
    cleaned_filters = validate_broadcast_filters(partner=partner, filters=filters)
    _validate_scheduled_at(scheduled_at)
    status = BroadcastCampaign.Status.SCHEDULED if scheduled_at else BroadcastCampaign.Status.DRAFT
    with transaction.atomic():
        campaign = BroadcastCampaign.objects.create(
            partner=partner,
            text=cleaned_text,
            photo=cleaned_photo,
            buttons=cleaned_buttons,
            filters=cleaned_filters,
            status=status,
            scheduled_at=scheduled_at,
            created_by=actor,
        )
        _record_campaign_audit(
            campaign=campaign,
            actor=actor,
            event_type="broadcast.created",
            message="Broadcast campaign created.",
        )
        if scheduled_at:
            _record_campaign_audit(
                campaign=campaign,
                actor=actor,
                event_type="broadcast.scheduled",
                message="Broadcast campaign scheduled.",
            )
    return campaign


def update_broadcast_campaign(
    *,
    actor: User,
    campaign: BroadcastCampaign,
    text: str = "",
    photo: str = "",
    buttons: list[dict[str, Any]] | None = None,
    filters: dict | None = None,
    scheduled_at=None,
) -> BroadcastCampaign:
    """Edit content/targeting only while a campaign has not started."""

    cleaned_text, cleaned_photo, cleaned_buttons = validate_broadcast_content(
        text=text,
        photo=photo,
        buttons=buttons,
    )
    _validate_scheduled_at(scheduled_at)
    with transaction.atomic():
        stored = (
            BroadcastCampaign.objects.select_for_update()
            .select_related("partner")
            .get(id=campaign.id)
        )
        ensure_partner_access(actor, stored.partner)
        if stored.status not in {
            BroadcastCampaign.Status.DRAFT,
            BroadcastCampaign.Status.SCHEDULED,
        }:
            raise ValidationError({"status": "A started broadcast cannot be edited."})
        cleaned_filters = validate_broadcast_filters(
            partner=stored.partner,
            filters=filters,
        )
        was_scheduled_for = stored.scheduled_at
        stored.text = cleaned_text
        stored.photo = cleaned_photo
        stored.buttons = cleaned_buttons
        stored.filters = cleaned_filters
        stored.scheduled_at = scheduled_at
        stored.status = (
            BroadcastCampaign.Status.SCHEDULED if scheduled_at else BroadcastCampaign.Status.DRAFT
        )
        stored.save(
            update_fields=[
                "text",
                "photo",
                "buttons",
                "filters",
                "scheduled_at",
                "status",
                "updated_at",
            ]
        )
        _record_campaign_audit(
            campaign=stored,
            actor=actor,
            event_type="broadcast.updated",
            message="Broadcast campaign updated.",
        )
        if scheduled_at and scheduled_at != was_scheduled_for:
            _record_campaign_audit(
                campaign=stored,
                actor=actor,
                event_type="broadcast.scheduled",
                message="Broadcast campaign scheduled.",
            )
    return stored


def queue_broadcast_campaign(
    *,
    actor: User,
    campaign: BroadcastCampaign,
    now=None,
) -> BroadcastCampaign:
    """Materialize eligible recipients and queue a broadcast for gradual sending."""

    ensure_partner_access(actor, campaign.partner)
    queued, _started = _queue_broadcast_campaign(
        campaign_id=campaign.id,
        actor=actor,
        now=now or timezone.now(),
        require_due=False,
    )
    return queued


def queue_due_scheduled_broadcast_campaigns(*, now=None, limit: int = 100) -> int:
    """Queue due scheduled campaigns idempotently for the broadcast worker."""

    current_time = now or timezone.now()
    campaign_ids = list(
        BroadcastCampaign.objects.filter(
            status=BroadcastCampaign.Status.SCHEDULED,
            scheduled_at__lte=current_time,
        )
        .order_by("scheduled_at", "created_at")
        .values_list("id", flat=True)[: max(1, limit)]
    )
    started_count = 0
    for campaign_id in campaign_ids:
        _campaign, started = _queue_broadcast_campaign(
            campaign_id=campaign_id,
            actor=None,
            now=current_time,
            require_due=True,
        )
        started_count += int(started)
    return started_count


def _queue_broadcast_campaign(
    *,
    campaign_id,
    actor: User | None,
    now,
    require_due: bool,
) -> tuple[BroadcastCampaign, bool]:
    """Materialize recipients once under a campaign lock."""

    with transaction.atomic():
        locked = (
            BroadcastCampaign.objects.select_for_update()
            .select_related("partner")
            .get(id=campaign_id)
        )
        if actor is not None:
            ensure_partner_access(actor, locked.partner)
        if require_due:
            if (
                locked.status != BroadcastCampaign.Status.SCHEDULED
                or locked.scheduled_at is None
                or locked.scheduled_at > now
            ):
                return locked, False
        elif locked.status == BroadcastCampaign.Status.QUEUED:
            return locked, False
        elif locked.status not in {
            BroadcastCampaign.Status.DRAFT,
            BroadcastCampaign.Status.SCHEDULED,
        }:
            raise ValidationError({"status": "Campaign cannot be queued from this status."})

        clients = list(
            eligible_broadcast_clients(
                partner=locked.partner,
                filters=locked.filters,
                now=now,
            )
        )
        for client in clients:
            BroadcastRecipient.objects.get_or_create(
                partner=locked.partner,
                campaign=locked,
                client_profile=client,
                defaults={
                    "status": BroadcastRecipient.Status.QUEUED,
                    "next_attempt_at": now,
                    "max_attempts": DEFAULT_BROADCAST_MAX_ATTEMPTS,
                    "retain_until": now + timedelta(days=BROADCAST_RECIPIENT_RETAIN_DAYS),
                },
            )
        locked.started_at = locked.started_at or now
        locked.status = BroadcastCampaign.Status.QUEUED
        locked.save(update_fields=["status", "started_at", "updated_at"])
        _refresh_campaign_counters(locked)
        _record_campaign_audit(
            campaign=locked,
            actor=actor,
            event_type="broadcast.started",
            message="Broadcast campaign started.",
        )
        _finalize_campaign_if_done(locked)
    locked.refresh_from_db()
    return locked, True


def cancel_broadcast_campaign(*, actor: User, campaign: BroadcastCampaign) -> BroadcastCampaign:
    """Cancel a campaign and skip recipients that have not been sent yet."""

    ensure_partner_access(actor, campaign.partner)
    now = timezone.now()
    with transaction.atomic():
        locked = (
            BroadcastCampaign.objects.select_for_update()
            .select_related("partner")
            .get(id=campaign.id)
        )
        ensure_partner_access(actor, locked.partner)
        if locked.status == BroadcastCampaign.Status.CANCELLED:
            return locked
        if locked.status in {
            BroadcastCampaign.Status.COMPLETED,
            BroadcastCampaign.Status.FAILED_PARTIAL,
        }:
            raise ValidationError({"status": "A finished broadcast cannot be cancelled."})
        locked.status = BroadcastCampaign.Status.CANCELLED
        locked.finished_at = now
        locked.save(update_fields=["status", "finished_at", "updated_at"])
        BroadcastRecipient.objects.filter(
            campaign=locked,
            status__in=[BroadcastRecipient.Status.QUEUED, BroadcastRecipient.Status.SENDING],
        ).update(status=BroadcastRecipient.Status.SKIPPED, updated_at=now)
        _refresh_campaign_counters(locked)
        _record_campaign_audit(
            campaign=locked,
            actor=actor,
            event_type="broadcast.cancelled",
            message="Broadcast campaign cancelled.",
        )
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
        campaign = (
            BroadcastCampaign.objects.select_for_update()
            .select_related("partner")
            .get(id=recipient.campaign_id)
        )
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
        locked.campaign = campaign
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
            _refresh_campaign_counters(campaign)
            _finalize_campaign_if_done(campaign)
            return None
        locked.status = BroadcastRecipient.Status.SENDING
        campaign.status = BroadcastCampaign.Status.SENDING
        locked.save(update_fields=["status", "updated_at"])
        campaign.save(update_fields=["status", "updated_at"])
        return locked


def _record_broadcast_delivery_result(
    *,
    recipient: BroadcastRecipient,
    result: BroadcastDeliveryResult,
    now,
) -> str:
    with transaction.atomic():
        campaign = (
            BroadcastCampaign.objects.select_for_update()
            .select_related("partner")
            .get(id=recipient.campaign_id)
        )
        locked = (
            BroadcastRecipient.objects.select_for_update()
            .select_related("partner", "campaign", "client_profile")
            .get(id=recipient.id)
        )
        locked.campaign = campaign
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
        _refresh_campaign_counters(campaign)
        _finalize_campaign_if_done(campaign)
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
    return client.marketing_opt_out or client.bot_unreachable or not client.telegram_identity_id


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
    stored = BroadcastCampaign.objects.select_for_update().get(id=campaign.id)
    counts = {
        status: BroadcastRecipient.objects.filter(campaign=stored, status=status).count()
        for status in BroadcastRecipient.Status.values
    }
    stored.queued_count = counts.get(BroadcastRecipient.Status.QUEUED, 0) + counts.get(
        BroadcastRecipient.Status.SENDING,
        0,
    )
    stored.sent_count = counts.get(BroadcastRecipient.Status.SENT, 0)
    stored.failed_count = counts.get(BroadcastRecipient.Status.FAILED, 0)
    stored.skipped_count = counts.get(BroadcastRecipient.Status.SKIPPED, 0)
    stored.save(
        update_fields=[
            "queued_count",
            "sent_count",
            "failed_count",
            "skipped_count",
            "updated_at",
        ]
    )
    _copy_campaign_counters(target=campaign, source=stored)


def _finalize_campaign_if_done(campaign: BroadcastCampaign) -> None:
    stored = (
        BroadcastCampaign.objects.select_for_update().select_related("partner").get(id=campaign.id)
    )
    if stored.status in {
        BroadcastCampaign.Status.CANCELLED,
        BroadcastCampaign.Status.COMPLETED,
        BroadcastCampaign.Status.FAILED_PARTIAL,
    }:
        return
    has_pending = BroadcastRecipient.objects.filter(
        campaign=stored,
        status__in=[BroadcastRecipient.Status.QUEUED, BroadcastRecipient.Status.SENDING],
    ).exists()
    if has_pending:
        return

    stored.status = (
        BroadcastCampaign.Status.FAILED_PARTIAL
        if stored.failed_count
        else BroadcastCampaign.Status.COMPLETED
    )
    stored.finished_at = timezone.now()
    stored.save(update_fields=["status", "finished_at", "updated_at"])
    _record_campaign_audit(
        campaign=stored,
        actor=None,
        event_type=(
            "broadcast.failed"
            if stored.status == BroadcastCampaign.Status.FAILED_PARTIAL
            else "broadcast.completed"
        ),
        message=(
            "Broadcast campaign completed with failed recipients."
            if stored.status == BroadcastCampaign.Status.FAILED_PARTIAL
            else "Broadcast campaign completed."
        ),
    )
    campaign.status = stored.status
    campaign.finished_at = stored.finished_at


def validate_broadcast_content(
    *,
    text: str,
    photo: str,
    buttons: list[dict[str, Any]] | None,
) -> tuple[str, str, list[dict[str, Any]]]:
    cleaned_text = text.strip()
    cleaned_photo = photo.strip()
    if not cleaned_text and not cleaned_photo:
        raise ValidationError({"text": "Broadcast needs text or photo."})
    text_limit = TELEGRAM_PHOTO_CAPTION_LIMIT if cleaned_photo else TELEGRAM_MESSAGE_TEXT_LIMIT
    if len(cleaned_text) > text_limit:
        raise ValidationError(
            {
                "text": (
                    f"Broadcast text must be {text_limit} characters or shorter"
                    + (" when a photo is attached." if cleaned_photo else ".")
                )
            }
        )
    cleaned_buttons = _clean_broadcast_buttons(buttons or [])
    return cleaned_text, cleaned_photo, cleaned_buttons


def validate_broadcast_filters(*, partner: Partner, filters: dict | None) -> dict:
    """Normalize the finite MVP audience filter registry."""

    if filters is None:
        return {}
    if not isinstance(filters, dict):
        raise ValidationError({"filters": "Broadcast filters must be an object."})
    unknown_keys = set(filters) - ALLOWED_BROADCAST_FILTER_KEYS
    if unknown_keys:
        raise ValidationError(
            {"filters": f"Unsupported broadcast filter: {sorted(unknown_keys)[0]}."}
        )

    cleaned: dict[str, Any] = {}
    service_id = filters.get("service_id")
    if service_id:
        try:
            service = Service.objects.filter(partner=partner, id=service_id).first()
        except (ValidationError, ValueError) as exc:
            raise ValidationError({"service_id": "Choose a valid partner service."}) from exc
        if service is None:
            raise ValidationError({"service_id": "Service must belong to this partner."})
        cleaned["service_id"] = str(service.id)

    long_absent_days = filters.get("long_absent_days")
    if long_absent_days is not None and long_absent_days != "":
        try:
            normalized_days = int(long_absent_days)
        except (TypeError, ValueError) as exc:
            raise ValidationError({"long_absent_days": "Enter a whole number of days."}) from exc
        if not 1 <= normalized_days <= MAX_LONG_ABSENT_DAYS:
            raise ValidationError(
                {"long_absent_days": (f"Days must be between 1 and {MAX_LONG_ABSENT_DAYS}.")}
            )
        cleaned["long_absent_days"] = normalized_days

    for key in ("has_future_appointment", "without_future_appointment"):
        value = filters.get(key)
        if value is not None and value is not True and value is not False:
            raise ValidationError({"filters": f"{key} must be true or false."})
    has_future = filters.get("has_future_appointment") is True
    without_future = filters.get("without_future_appointment") is True
    if has_future and without_future:
        raise ValidationError({"filters": "Choose only one future-appointment filter."})
    if has_future:
        cleaned["has_future_appointment"] = True
    if without_future:
        cleaned["without_future_appointment"] = True
    return cleaned


def _validate_scheduled_at(scheduled_at) -> None:
    if scheduled_at is None:
        return
    if timezone.is_naive(scheduled_at):
        raise ValidationError({"scheduled_at": "Scheduled time must include a timezone."})
    if scheduled_at <= timezone.now():
        raise ValidationError({"scheduled_at": "Scheduled time must be in the future."})


def _record_campaign_audit(
    *,
    campaign: BroadcastCampaign,
    actor: User | None,
    event_type: str,
    message: str,
) -> None:
    record_audit_event(
        partner=campaign.partner,
        actor=actor,
        event_type=event_type,
        target=campaign,
        message=message,
        payload={
            "status": campaign.status,
            "scheduled_at": (campaign.scheduled_at.isoformat() if campaign.scheduled_at else None),
            "queued_count": campaign.queued_count,
            "sent_count": campaign.sent_count,
            "failed_count": campaign.failed_count,
            "skipped_count": campaign.skipped_count,
        },
    )


def _copy_campaign_counters(*, target: BroadcastCampaign, source: BroadcastCampaign) -> None:
    target.queued_count = source.queued_count
    target.sent_count = source.sent_count
    target.failed_count = source.failed_count
    target.skipped_count = source.skipped_count


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
        if button_type == "external_link":
            try:
                validate_external_link_url(url)
            except ValidationError as exc:
                raise ValidationError(
                    {"buttons": "External link button needs a valid HTTP(S) URL."}
                ) from exc
        cleaned_button = {"type": button_type, "label": label}
        if button_type == "external_link":
            cleaned_button["url"] = url
        cleaned.append(cleaned_button)
    return cleaned
