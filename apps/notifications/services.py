from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from datetime import timezone as datetime_timezone
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.appointments.models import Appointment, AppointmentProposal
from apps.clients.services import mark_client_unreachable
from apps.notifications.models import (
    DEFAULT_CLIENT_REMINDER_OFFSETS_MINUTES,
    DEFAULT_MASTER_REMINDER_OFFSET_MINUTES,
    DEFAULT_MORNING_DIGEST_TIME_LOCAL,
    DEFAULT_NOTIFICATION_MAX_ATTEMPTS,
    MIN_STALE_REQUESTED_REPEAT_MINUTES,
    NOTIFICATION_RETAIN_DAYS,
    DeliveryAttempt,
    PartnerNotificationSettings,
    ReminderRule,
    ScheduledNotification,
)
from apps.notifications.selectors import active_reminder_rules_for_partner, due_notifications
from core.security.masking import mask_sensitive_text
from core.timezones import get_zone

if TYPE_CHECKING:
    from apps.partners.models import Partner


AUTOMATIC_RETRY_BACKOFFS = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15))
TERMINAL_TELEGRAM_FAILURE_CODES = {"bot_blocked", "user_deactivated", "chat_not_found"}
QUEUED_OR_SENDING_STATUSES = (
    ScheduledNotification.Status.QUEUED,
    ScheduledNotification.Status.SENDING,
)
REQUEST_ACTION_NOTIFICATION_TYPES = (
    ScheduledNotification.Type.MASTER_BOOKING_REQUEST,
    ScheduledNotification.Type.STALE_REQUESTED,
)
CLIENT_TARGET_NOTIFICATION_TYPES = frozenset(
    {
        ScheduledNotification.Type.CLIENT_APPOINTMENT_CREATED,
        ScheduledNotification.Type.CLIENT_BOOKING_REQUEST_CONFIRMED,
        ScheduledNotification.Type.CLIENT_BOOKING_REQUEST_DECLINED,
        ScheduledNotification.Type.CLIENT_APPOINTMENT_REMINDER,
        ScheduledNotification.Type.CLIENT_APPOINTMENT_PROPOSAL,
        ScheduledNotification.Type.CLIENT_APPOINTMENT_PROPOSAL_ACCEPTED,
        ScheduledNotification.Type.CLIENT_APPOINTMENT_PROPOSAL_DECLINED,
        ScheduledNotification.Type.CLIENT_APPOINTMENT_PROPOSAL_EXPIRED,
    }
)


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """Adapter-neutral result returned by a notification delivery backend."""

    success: bool
    failure_code: str = ""
    failure_message: str = ""
    terminal: bool = False
    skipped: bool = False
    retry_after_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class DispatchSummary:
    sent: int = 0
    failed: int = 0
    queued_for_retry: int = 0
    skipped: int = 0
    ignored: int = 0

    def add(self, status: str) -> DispatchSummary:
        if status == ScheduledNotification.Status.SENT:
            return DispatchSummary(
                sent=self.sent + 1,
                failed=self.failed,
                queued_for_retry=self.queued_for_retry,
                skipped=self.skipped,
                ignored=self.ignored,
            )
        if status == ScheduledNotification.Status.FAILED:
            return DispatchSummary(
                sent=self.sent,
                failed=self.failed + 1,
                queued_for_retry=self.queued_for_retry,
                skipped=self.skipped,
                ignored=self.ignored,
            )
        if status == ScheduledNotification.Status.QUEUED:
            return DispatchSummary(
                sent=self.sent,
                failed=self.failed,
                queued_for_retry=self.queued_for_retry + 1,
                skipped=self.skipped,
                ignored=self.ignored,
            )
        if status in {ScheduledNotification.Status.SKIPPED, ScheduledNotification.Status.STALE}:
            return DispatchSummary(
                sent=self.sent,
                failed=self.failed,
                queued_for_retry=self.queued_for_retry,
                skipped=self.skipped + 1,
                ignored=self.ignored,
            )
        return DispatchSummary(
            sent=self.sent,
            failed=self.failed,
            queued_for_retry=self.queued_for_retry,
            skipped=self.skipped,
            ignored=self.ignored + 1,
        )


DeliverNotification = Callable[[ScheduledNotification], DeliveryResult]


def get_or_create_notification_settings(*, partner: Partner) -> PartnerNotificationSettings:
    """Return partner notification settings with MVP defaults."""

    settings, _created = PartnerNotificationSettings.objects.get_or_create(partner=partner)
    return settings


def ensure_default_reminder_rules(*, partner: Partner) -> tuple[ReminderRule, ...]:
    """Create default client/master reminder rules if they do not exist yet."""

    defaults = [
        (ReminderRule.Target.CLIENT, offset) for offset in DEFAULT_CLIENT_REMINDER_OFFSETS_MINUTES
    ]
    defaults.append((ReminderRule.Target.MASTER, DEFAULT_MASTER_REMINDER_OFFSET_MINUTES))

    rules: list[ReminderRule] = []
    for target, offset in defaults:
        rule, _created = ReminderRule.objects.get_or_create(
            partner=partner,
            target=target,
            offset_minutes=offset,
            defaults={"is_active": True},
        )
        rules.append(rule)
    return tuple(rules)


def schedule_reminders_for_confirmed_appointment(
    *,
    appointment: Appointment,
    now: datetime | None = None,
) -> tuple[ScheduledNotification, ...]:
    """Schedule client/master appointment reminders for a confirmed appointment."""

    if appointment.status != Appointment.Status.CONFIRMED:
        return ()

    current_time = now or timezone.now()
    settings = get_or_create_notification_settings(partner=appointment.partner)
    ensure_default_reminder_rules(partner=appointment.partner)
    scheduled: list[ScheduledNotification] = []

    client_has_telegram = bool(appointment.client_profile.telegram_identity_id)
    for rule in active_reminder_rules_for_partner(partner=appointment.partner):
        if rule.target == ReminderRule.Target.CLIENT and not client_has_telegram:
            continue
        due_at = appointment.start_at - timedelta(minutes=rule.offset_minutes)
        due_at = apply_quiet_hours(
            due_at=due_at,
            partner=appointment.partner,
            settings=settings,
        )
        notification_type = _notification_type_for_reminder_rule(rule)
        scheduled.append(
            _upsert_scheduled_notification(
                partner=appointment.partner,
                appointment=appointment,
                client_profile=appointment.client_profile,
                reminder_rule=rule,
                type=notification_type,
                due_at=due_at,
                idempotency_key=_appointment_reminder_idempotency_key(
                    appointment=appointment,
                    notification_type=notification_type,
                    offset_minutes=rule.offset_minutes,
                ),
                now=current_time,
            )
        )
    return tuple(scheduled)


def schedule_manual_appointment_created_notification(
    *,
    appointment: Appointment,
    now: datetime | None = None,
) -> ScheduledNotification | None:
    """Queue the immediate client notice for a partner-created appointment."""

    if (
        appointment.status != Appointment.Status.CONFIRMED
        or appointment.source != Appointment.Source.PARTNER_ADMIN
        or not appointment.client_profile.telegram_identity_id
    ):
        return None

    current_time = now or timezone.now()
    due_at = apply_quiet_hours(
        due_at=current_time,
        partner=appointment.partner,
    )
    return _upsert_scheduled_notification(
        partner=appointment.partner,
        appointment=appointment,
        client_profile=appointment.client_profile,
        reminder_rule=None,
        type=ScheduledNotification.Type.CLIENT_APPOINTMENT_CREATED,
        due_at=due_at,
        idempotency_key=f"appointment:{appointment.id}:client_created",
        now=current_time,
    )


def schedule_master_booking_request_notification(
    *,
    appointment: Appointment,
    request_revision: str = "",
    now: datetime | None = None,
) -> ScheduledNotification | None:
    """Queue the initial actionable master notice for a client booking request."""

    if appointment.status != Appointment.Status.REQUESTED:
        return None

    current_time = now or timezone.now()
    due_at = apply_quiet_hours(
        due_at=current_time,
        partner=appointment.partner,
    )
    idempotency_key = f"appointment:{appointment.id}:master:booking_request"
    if request_revision:
        idempotency_key = f"{idempotency_key}:{request_revision}"
    return _upsert_scheduled_notification(
        partner=appointment.partner,
        appointment=appointment,
        client_profile=appointment.client_profile,
        reminder_rule=None,
        type=ScheduledNotification.Type.MASTER_BOOKING_REQUEST,
        due_at=due_at,
        idempotency_key=idempotency_key,
        now=current_time,
    )


def schedule_client_booking_request_confirmed_notification(
    *,
    appointment: Appointment,
    now: datetime | None = None,
) -> ScheduledNotification | None:
    """Queue the client notice produced when a master confirms a request."""

    if (
        appointment.status != Appointment.Status.CONFIRMED
        or not appointment.client_profile.telegram_identity_id
    ):
        return None

    current_time = now or timezone.now()
    due_at = apply_quiet_hours(due_at=current_time, partner=appointment.partner)
    return _upsert_scheduled_notification(
        partner=appointment.partner,
        appointment=appointment,
        client_profile=appointment.client_profile,
        reminder_rule=None,
        type=ScheduledNotification.Type.CLIENT_BOOKING_REQUEST_CONFIRMED,
        due_at=due_at,
        idempotency_key=f"appointment:{appointment.id}:client:request_confirmed",
        now=current_time,
    )


def schedule_client_booking_request_declined_notification(
    *,
    appointment: Appointment,
    now: datetime | None = None,
) -> ScheduledNotification | None:
    """Queue the client notice produced when a master declines a request."""

    if (
        appointment.status != Appointment.Status.CANCELLED_BY_MASTER
        or not appointment.client_profile.telegram_identity_id
    ):
        return None

    current_time = now or timezone.now()
    due_at = apply_quiet_hours(due_at=current_time, partner=appointment.partner)
    return _upsert_scheduled_notification(
        partner=appointment.partner,
        appointment=appointment,
        client_profile=appointment.client_profile,
        reminder_rule=None,
        type=ScheduledNotification.Type.CLIENT_BOOKING_REQUEST_DECLINED,
        due_at=due_at,
        idempotency_key=f"appointment:{appointment.id}:client:request_declined",
        now=current_time,
    )


def schedule_master_client_cancelled_notification(
    *,
    appointment: Appointment,
    now: datetime | None = None,
) -> ScheduledNotification | None:
    """Queue the immediate master notice for a client cancellation."""

    if appointment.status != Appointment.Status.CANCELLED_BY_CLIENT:
        return None

    current_time = now or timezone.now()
    due_at = apply_quiet_hours(
        due_at=current_time,
        partner=appointment.partner,
    )
    status_version = appointment.status_changed_at or current_time
    return _upsert_scheduled_notification(
        partner=appointment.partner,
        appointment=appointment,
        client_profile=appointment.client_profile,
        reminder_rule=None,
        type=ScheduledNotification.Type.MASTER_CLIENT_CANCELLED_APPOINTMENT,
        due_at=due_at,
        idempotency_key=(
            f"appointment:{appointment.id}:master_client_cancelled:{status_version.isoformat()}"
        ),
        now=current_time,
    )


def schedule_master_client_reschedule_notification(
    *,
    appointment: Appointment,
    proposal: AppointmentProposal | None = None,
    now: datetime | None = None,
) -> ScheduledNotification:
    """Queue a master notice for an auto-move or a pending client request."""

    if proposal is not None:
        if (
            proposal.appointment_id != appointment.id
            or proposal.status != AppointmentProposal.Status.PENDING
            or proposal.created_by_id is not None
        ):
            raise ValidationError({"proposal": "Proposal is not a pending client request."})
        idempotency_key = f"proposal:{proposal.id}:master:client_requested"
    else:
        if appointment.status != Appointment.Status.CONFIRMED:
            raise ValidationError(
                {"appointment": "Auto-rescheduled appointment must be confirmed."}
            )
        idempotency_key = (
            f"appointment:{appointment.id}:master:client_rescheduled:"
            f"{appointment.start_at.isoformat()}"
        )

    current_time = now or timezone.now()
    due_at = apply_quiet_hours(due_at=current_time, partner=appointment.partner)
    return _upsert_scheduled_notification(
        partner=appointment.partner,
        appointment=appointment,
        appointment_proposal=proposal,
        client_profile=appointment.client_profile,
        reminder_rule=None,
        type=ScheduledNotification.Type.MASTER_CLIENT_REQUESTED_RESCHEDULE,
        due_at=due_at,
        idempotency_key=idempotency_key,
        now=current_time,
    )


def schedule_client_appointment_proposal_notification(
    *,
    proposal: AppointmentProposal,
    now: datetime | None = None,
) -> ScheduledNotification | None:
    """Queue a client-visible appointment change proposal."""

    if (
        proposal.status != AppointmentProposal.Status.PENDING
        or not proposal.appointment.client_profile.telegram_identity_id
    ):
        return None

    current_time = now or timezone.now()
    due_at = apply_quiet_hours(
        due_at=current_time,
        partner=proposal.partner,
    )
    return _upsert_scheduled_notification(
        partner=proposal.partner,
        appointment=proposal.appointment,
        appointment_proposal=proposal,
        client_profile=proposal.appointment.client_profile,
        reminder_rule=None,
        type=ScheduledNotification.Type.CLIENT_APPOINTMENT_PROPOSAL,
        due_at=due_at,
        idempotency_key=f"proposal:{proposal.id}:client_created",
        now=current_time,
    )


def schedule_master_proposal_response_notification(
    *,
    proposal: AppointmentProposal,
    now: datetime | None = None,
) -> ScheduledNotification | None:
    """Queue the accepted/declined proposal outcome for the partner contact."""

    type_by_status = {
        AppointmentProposal.Status.ACCEPTED: (
            ScheduledNotification.Type.MASTER_CLIENT_ACCEPTED_PROPOSAL
        ),
        AppointmentProposal.Status.DECLINED: (
            ScheduledNotification.Type.MASTER_CLIENT_DECLINED_PROPOSAL
        ),
    }
    notification_type = type_by_status.get(proposal.status)
    if notification_type is None:
        return None

    current_time = now or timezone.now()
    due_at = apply_quiet_hours(
        due_at=current_time,
        partner=proposal.partner,
    )
    return _upsert_scheduled_notification(
        partner=proposal.partner,
        appointment=proposal.appointment,
        appointment_proposal=proposal,
        client_profile=proposal.appointment.client_profile,
        reminder_rule=None,
        type=notification_type,
        due_at=due_at,
        idempotency_key=f"proposal:{proposal.id}:master:{proposal.status}",
        now=current_time,
    )


def schedule_client_proposal_response_notification(
    *,
    proposal: AppointmentProposal,
    now: datetime | None = None,
) -> ScheduledNotification | None:
    """Queue the partner's accepted/declined response to a client request."""

    if proposal.created_by_id is not None:
        return None
    type_by_status = {
        AppointmentProposal.Status.ACCEPTED: (
            ScheduledNotification.Type.CLIENT_APPOINTMENT_PROPOSAL_ACCEPTED
        ),
        AppointmentProposal.Status.DECLINED: (
            ScheduledNotification.Type.CLIENT_APPOINTMENT_PROPOSAL_DECLINED
        ),
    }
    notification_type = type_by_status.get(proposal.status)
    if notification_type is None or not proposal.appointment.client_profile.telegram_identity_id:
        return None

    current_time = now or timezone.now()
    due_at = apply_quiet_hours(due_at=current_time, partner=proposal.partner)
    return _upsert_scheduled_notification(
        partner=proposal.partner,
        appointment=proposal.appointment,
        appointment_proposal=proposal,
        client_profile=proposal.appointment.client_profile,
        reminder_rule=None,
        type=notification_type,
        due_at=due_at,
        idempotency_key=f"proposal:{proposal.id}:client:{proposal.status}",
        now=current_time,
    )


def schedule_expired_proposal_notifications(
    *,
    proposal: AppointmentProposal,
    now: datetime | None = None,
) -> tuple[ScheduledNotification, ...]:
    """Queue expiry notices for every reachable side of an expired proposal."""

    if proposal.status != AppointmentProposal.Status.EXPIRED:
        return ()

    current_time = now or timezone.now()
    due_at = apply_quiet_hours(
        due_at=current_time,
        partner=proposal.partner,
    )
    scheduled = [
        _upsert_scheduled_notification(
            partner=proposal.partner,
            appointment=proposal.appointment,
            appointment_proposal=proposal,
            client_profile=proposal.appointment.client_profile,
            reminder_rule=None,
            type=ScheduledNotification.Type.MASTER_APPOINTMENT_PROPOSAL_EXPIRED,
            due_at=due_at,
            idempotency_key=f"proposal:{proposal.id}:master:expired",
            now=current_time,
        )
    ]
    if proposal.appointment.client_profile.telegram_identity_id:
        scheduled.append(
            _upsert_scheduled_notification(
                partner=proposal.partner,
                appointment=proposal.appointment,
                appointment_proposal=proposal,
                client_profile=proposal.appointment.client_profile,
                reminder_rule=None,
                type=ScheduledNotification.Type.CLIENT_APPOINTMENT_PROPOSAL_EXPIRED,
                due_at=due_at,
                idempotency_key=f"proposal:{proposal.id}:client:expired",
                now=current_time,
            )
        )
    return tuple(scheduled)


def schedule_stale_requested_notification(
    *,
    appointment: Appointment,
    sequence: int = 0,
    anchor_at: datetime | None = None,
) -> ScheduledNotification | None:
    """Schedule the next stale requested reminder for the master."""

    if appointment.status != Appointment.Status.REQUESTED or _has_pending_requested_alternatives(
        appointment
    ):
        return None

    settings = get_or_create_notification_settings(partner=appointment.partner)
    anchor = anchor_at or appointment.requested_at or appointment.created_at
    interval = stale_requested_interval_for_sequence(
        base_minutes=settings.stale_requested_base_interval_minutes,
        sequence=sequence,
    )
    due_at = apply_quiet_hours(
        due_at=anchor + timedelta(minutes=interval),
        partner=appointment.partner,
        settings=settings,
    )
    return _upsert_scheduled_notification(
        partner=appointment.partner,
        appointment=appointment,
        client_profile=appointment.client_profile,
        reminder_rule=None,
        type=ScheduledNotification.Type.STALE_REQUESTED,
        due_at=due_at,
        idempotency_key=f"appointment:{appointment.id}:stale_requested:{sequence}",
        stale_sequence=sequence,
        now=timezone.now(),
    )


def resume_stale_requested_notification(
    *,
    appointment: Appointment,
    anchor_at: datetime | None = None,
) -> ScheduledNotification | None:
    """Continue the reminder sequence after a client declines/cancels alternatives."""

    last_sequence = (
        ScheduledNotification.objects.filter(
            appointment=appointment,
            type=ScheduledNotification.Type.STALE_REQUESTED,
        )
        .order_by("-stale_sequence")
        .values_list("stale_sequence", flat=True)
        .first()
    )
    return schedule_stale_requested_notification(
        appointment=appointment,
        sequence=(last_sequence + 1 if last_sequence is not None else 0),
        anchor_at=anchor_at,
    )


def schedule_morning_digest(
    *,
    partner: Partner,
    digest_date: date,
) -> ScheduledNotification | None:
    """Schedule a partner morning digest for one local date."""

    settings = get_or_create_notification_settings(partner=partner)
    if not settings.morning_digest_enabled:
        return None

    partner_zone = get_zone(partner.timezone)
    local_due_at = datetime.combine(
        digest_date,
        settings.morning_digest_time_local or DEFAULT_MORNING_DIGEST_TIME_LOCAL,
        tzinfo=partner_zone,
    )
    due_at = local_due_at.astimezone(datetime_timezone.utc)
    return _upsert_scheduled_notification(
        partner=partner,
        appointment=None,
        client_profile=None,
        reminder_rule=None,
        type=ScheduledNotification.Type.MASTER_MORNING_DIGEST,
        due_at=due_at,
        idempotency_key=f"partner:{partner.id}:morning_digest:{digest_date.isoformat()}",
        now=timezone.now(),
    )


def cancel_pending_notifications_for_appointment(
    *,
    appointment: Appointment,
    status: str = ScheduledNotification.Status.STALE,
) -> int:
    """Make old pending reminders unable to send after appointment changes."""

    if status not in {
        ScheduledNotification.Status.CANCELLED,
        ScheduledNotification.Status.STALE,
        ScheduledNotification.Status.SKIPPED,
    }:
        raise ValidationError({"status": "Unsupported cancellation status."})

    return ScheduledNotification.objects.filter(
        appointment=appointment,
        status__in=QUEUED_OR_SENDING_STATUSES,
    ).update(status=status, updated_at=timezone.now())


def stale_pending_request_notifications(*, appointment: Appointment) -> int:
    """Stop request-action notices while the client considers offered alternatives."""

    return ScheduledNotification.objects.filter(
        appointment=appointment,
        type__in=REQUEST_ACTION_NOTIFICATION_TYPES,
        status__in=QUEUED_OR_SENDING_STATUSES,
    ).update(status=ScheduledNotification.Status.STALE, updated_at=timezone.now())


def dispatch_due_notifications(
    *,
    deliver: DeliverNotification,
    now: datetime | None = None,
    limit: int = 100,
) -> DispatchSummary:
    """Dispatch due queued notifications with durable idempotent state."""

    current_time = now or timezone.now()
    summary = DispatchSummary()
    notification_ids = list(
        due_notifications(now=current_time).values_list("id", flat=True)[:limit]
    )
    for notification_id in notification_ids:
        notification = ScheduledNotification.objects.get(id=notification_id)
        final_status = dispatch_notification(
            notification=notification,
            deliver=deliver,
            now=current_time,
        )
        summary = summary.add(final_status)
    return summary


def dispatch_notification(
    *,
    notification: ScheduledNotification,
    deliver: DeliverNotification,
    now: datetime | None = None,
) -> str:
    """Dispatch one notification without holding DB locks during delivery I/O."""

    current_time = now or timezone.now()
    prepared = _prepare_notification_for_delivery(
        notification=notification,
        now=current_time,
    )
    if prepared is None:
        return ScheduledNotification.Status.SKIPPED

    result = deliver(prepared)
    final_status = _record_delivery_result(
        notification=prepared,
        result=result,
        now=current_time,
    )
    if (
        final_status == ScheduledNotification.Status.SENT
        and prepared.type == ScheduledNotification.Type.STALE_REQUESTED
    ):
        prepared.refresh_from_db()
        if prepared.appointment and prepared.appointment.status == Appointment.Status.REQUESTED:
            schedule_stale_requested_notification(
                appointment=prepared.appointment,
                sequence=prepared.stale_sequence + 1,
                anchor_at=current_time,
            )
    return final_status


def retry_failed_notification(
    *,
    notification: ScheduledNotification,
    deliver: DeliverNotification | None = None,
    now: datetime | None = None,
) -> ScheduledNotification:
    """Queue or immediately retry a failed notification after a master action."""

    current_time = now or timezone.now()
    with transaction.atomic():
        locked = ScheduledNotification.objects.select_for_update().get(id=notification.id)
        if locked.status != ScheduledNotification.Status.FAILED:
            raise ValidationError({"status": "Only failed notifications can be retried."})
        locked.status = ScheduledNotification.Status.QUEUED
        locked.due_at = current_time
        locked.save(update_fields=["status", "due_at", "updated_at"])

    if deliver is not None:
        dispatch_notification(notification=locked, deliver=deliver, now=current_time)
        locked.refresh_from_db()
    return locked


def apply_quiet_hours(
    *,
    due_at: datetime,
    partner: Partner,
    settings: PartnerNotificationSettings | None = None,
) -> datetime:
    """Move a due time to the first moment after quiet hours if needed."""

    if timezone.is_naive(due_at):
        raise ValidationError({"due_at": "Datetime must be timezone-aware."})

    settings = settings or get_or_create_notification_settings(partner=partner)
    if not settings.quiet_hours_enabled:
        return due_at

    partner_zone = get_zone(partner.timezone)
    local_due = due_at.astimezone(partner_zone)
    start = settings.quiet_hours_start_local
    end = settings.quiet_hours_end_local
    if not _is_inside_quiet_hours(local_due.time(), start=start, end=end):
        return due_at

    quiet_end_date = local_due.date()
    if start > end and local_due.time() >= start:
        quiet_end_date += timedelta(days=1)

    adjusted = datetime.combine(quiet_end_date, end, tzinfo=partner_zone)
    return adjusted.astimezone(datetime_timezone.utc)


def stale_requested_interval_for_sequence(*, base_minutes: int, sequence: int) -> int:
    """Return configured stale-request interval halved down to the 5-minute floor."""

    if sequence < 0:
        raise ValidationError({"sequence": "Sequence cannot be negative."})
    interval = base_minutes / (2**sequence)
    return max(MIN_STALE_REQUESTED_REPEAT_MINUTES, int(interval))


def _prepare_notification_for_delivery(
    *,
    notification: ScheduledNotification,
    now: datetime,
) -> ScheduledNotification | None:
    with transaction.atomic():
        locked = (
            ScheduledNotification.objects.select_for_update()
            .select_related(
                "partner",
                "appointment",
                "appointment_proposal",
                "client_profile",
                "reminder_rule",
            )
            .get(id=notification.id)
        )
        if locked.status != ScheduledNotification.Status.QUEUED or locked.due_at > now:
            return None
        obsolete_status = _obsolete_status_for_notification(notification=locked, now=now)
        if obsolete_status is not None:
            locked.status = obsolete_status
            locked.save(update_fields=["status", "updated_at"])
            _record_delivery_attempt(
                notification=locked,
                status=DeliveryAttempt.Status.SKIPPED,
                attempted_at=now,
            )
            return None
        locked.status = ScheduledNotification.Status.SENDING
        locked.save(update_fields=["status", "updated_at"])
        return locked


def _record_delivery_result(
    *,
    notification: ScheduledNotification,
    result: DeliveryResult,
    now: datetime,
) -> str:
    with transaction.atomic():
        locked = (
            ScheduledNotification.objects.select_for_update()
            .select_related(
                "partner",
                "appointment",
                "appointment_proposal",
                "client_profile",
                "reminder_rule",
            )
            .get(id=notification.id)
        )
        if locked.status != ScheduledNotification.Status.SENDING:
            return locked.status

        if result.skipped:
            locked.status = ScheduledNotification.Status.SKIPPED
            _record_delivery_attempt(
                notification=locked,
                status=DeliveryAttempt.Status.SKIPPED,
                attempted_at=now,
            )
        elif result.success:
            locked.status = ScheduledNotification.Status.SENT
            locked.sent_at = now
            locked.failed_visible_to_partner = False
            locked.last_failure_code = ""
            locked.last_failure_message_masked = ""
            _record_delivery_attempt(
                notification=locked,
                status=DeliveryAttempt.Status.SENT,
                attempted_at=now,
            )
        else:
            _apply_delivery_failure(notification=locked, result=result, now=now)

        locked.save(
            update_fields=[
                "status",
                "due_at",
                "attempt_count",
                "last_failure_code",
                "last_failure_message_masked",
                "failed_visible_to_partner",
                "sent_at",
                "updated_at",
            ]
        )
        return locked.status


def _apply_delivery_failure(
    *,
    notification: ScheduledNotification,
    result: DeliveryResult,
    now: datetime,
) -> None:
    notification.attempt_count += 1
    notification.last_failure_code = result.failure_code[:128]
    notification.last_failure_message_masked = mask_sensitive_text(result.failure_message)
    terminal = result.terminal or result.failure_code in TERMINAL_TELEGRAM_FAILURE_CODES

    _record_delivery_attempt(
        notification=notification,
        status=DeliveryAttempt.Status.FAILED,
        attempted_at=now,
        failure_code=notification.last_failure_code,
        failure_message_masked=notification.last_failure_message_masked,
    )

    if (
        terminal
        and notification.client_profile_id
        and notification.type in CLIENT_TARGET_NOTIFICATION_TYPES
    ):
        mark_client_unreachable(
            client_profile=notification.client_profile,
            failure_code=notification.last_failure_code or "terminal_delivery_failure",
        )

    if terminal or notification.attempt_count >= notification.max_attempts:
        notification.status = ScheduledNotification.Status.FAILED
        notification.failed_visible_to_partner = True
        return

    backoff = AUTOMATIC_RETRY_BACKOFFS[
        min(notification.attempt_count - 1, len(AUTOMATIC_RETRY_BACKOFFS) - 1)
    ]
    notification.status = ScheduledNotification.Status.QUEUED
    notification.due_at = apply_quiet_hours(
        due_at=now + backoff,
        partner=notification.partner,
    )


def _record_delivery_attempt(
    *,
    notification: ScheduledNotification,
    status: str,
    attempted_at: datetime,
    failure_code: str = "",
    failure_message_masked: str = "",
) -> DeliveryAttempt:
    return DeliveryAttempt.objects.create(
        partner=notification.partner,
        notification=notification,
        status=status,
        failure_code=failure_code,
        failure_message_masked=failure_message_masked,
        attempted_at=attempted_at,
        retain_until=attempted_at + timedelta(days=NOTIFICATION_RETAIN_DAYS),
    )


def _upsert_scheduled_notification(
    *,
    partner: Partner,
    appointment: Appointment | None,
    appointment_proposal: AppointmentProposal | None = None,
    client_profile,
    reminder_rule: ReminderRule | None,
    type: str,
    due_at: datetime,
    idempotency_key: str,
    now: datetime,
    stale_sequence: int = 0,
) -> ScheduledNotification:
    if timezone.is_naive(due_at):
        raise ValidationError({"due_at": "Datetime must be timezone-aware."})
    notification, _created = ScheduledNotification.objects.get_or_create(
        idempotency_key=idempotency_key,
        defaults={
            "partner": partner,
            "appointment": appointment,
            "appointment_proposal": appointment_proposal,
            "client_profile": client_profile,
            "reminder_rule": reminder_rule,
            "type": type,
            "due_at": due_at,
            "status": ScheduledNotification.Status.QUEUED,
            "max_attempts": DEFAULT_NOTIFICATION_MAX_ATTEMPTS,
            "stale_sequence": stale_sequence,
            "retain_until": now + timedelta(days=NOTIFICATION_RETAIN_DAYS),
        },
    )
    return notification


def _notification_type_for_reminder_rule(rule: ReminderRule) -> str:
    if rule.target == ReminderRule.Target.CLIENT:
        return ScheduledNotification.Type.CLIENT_APPOINTMENT_REMINDER
    return ScheduledNotification.Type.MASTER_APPOINTMENT_REMINDER


def _appointment_reminder_idempotency_key(
    *,
    appointment: Appointment,
    notification_type: str,
    offset_minutes: int,
) -> str:
    return (
        f"appointment:{appointment.id}:{notification_type}:"
        f"{offset_minutes}:{appointment.start_at.isoformat()}"
    )


def _obsolete_status_for_notification(
    *,
    notification: ScheduledNotification,
    now: datetime,
) -> str | None:
    appointment = notification.appointment
    proposal = notification.appointment_proposal
    if notification.type == ScheduledNotification.Type.MASTER_MORNING_DIGEST:
        return None
    if notification.type == ScheduledNotification.Type.CLIENT_APPOINTMENT_PROPOSAL:
        if (
            proposal is None
            or proposal.status != AppointmentProposal.Status.PENDING
            or proposal.expires_at <= now
        ):
            return ScheduledNotification.Status.STALE
        return None
    if notification.type == ScheduledNotification.Type.MASTER_CLIENT_REQUESTED_RESCHEDULE:
        if proposal is not None:
            if proposal.status != AppointmentProposal.Status.PENDING:
                return ScheduledNotification.Status.STALE
            return None
        if appointment is None or appointment.status != Appointment.Status.CONFIRMED:
            return ScheduledNotification.Status.STALE
        if appointment.start_at <= now:
            return ScheduledNotification.Status.SKIPPED
        return None
    proposal_status_by_type = {
        ScheduledNotification.Type.CLIENT_APPOINTMENT_PROPOSAL_EXPIRED: (
            AppointmentProposal.Status.EXPIRED
        ),
        ScheduledNotification.Type.MASTER_APPOINTMENT_PROPOSAL_EXPIRED: (
            AppointmentProposal.Status.EXPIRED
        ),
        ScheduledNotification.Type.MASTER_CLIENT_ACCEPTED_PROPOSAL: (
            AppointmentProposal.Status.ACCEPTED
        ),
        ScheduledNotification.Type.MASTER_CLIENT_DECLINED_PROPOSAL: (
            AppointmentProposal.Status.DECLINED
        ),
        ScheduledNotification.Type.CLIENT_APPOINTMENT_PROPOSAL_ACCEPTED: (
            AppointmentProposal.Status.ACCEPTED
        ),
        ScheduledNotification.Type.CLIENT_APPOINTMENT_PROPOSAL_DECLINED: (
            AppointmentProposal.Status.DECLINED
        ),
    }
    expected_proposal_status = proposal_status_by_type.get(notification.type)
    if expected_proposal_status is not None:
        if proposal is None or proposal.status != expected_proposal_status:
            return ScheduledNotification.Status.STALE
        return None
    if appointment is None:
        return None
    if notification.type == ScheduledNotification.Type.MASTER_CLIENT_CANCELLED_APPOINTMENT:
        if appointment.status != Appointment.Status.CANCELLED_BY_CLIENT:
            return ScheduledNotification.Status.STALE
        return None
    if notification.type == ScheduledNotification.Type.MASTER_BOOKING_REQUEST:
        if appointment.status != Appointment.Status.REQUESTED:
            return ScheduledNotification.Status.STALE
        if _has_pending_requested_alternatives(appointment):
            return ScheduledNotification.Status.STALE
        if appointment.start_at <= now:
            return ScheduledNotification.Status.SKIPPED
        return None
    if notification.type == ScheduledNotification.Type.CLIENT_BOOKING_REQUEST_DECLINED:
        if appointment.status != Appointment.Status.CANCELLED_BY_MASTER:
            return ScheduledNotification.Status.STALE
        return None
    if notification.type == ScheduledNotification.Type.STALE_REQUESTED:
        if appointment.status != Appointment.Status.REQUESTED:
            return ScheduledNotification.Status.STALE
        if _has_pending_requested_alternatives(appointment):
            return ScheduledNotification.Status.STALE
        if appointment.start_at <= now:
            return ScheduledNotification.Status.SKIPPED
        return None
    if appointment.status != Appointment.Status.CONFIRMED:
        return ScheduledNotification.Status.STALE
    if appointment.start_at <= now:
        return ScheduledNotification.Status.SKIPPED
    return None


def _has_pending_requested_alternatives(appointment: Appointment) -> bool:
    return AppointmentProposal.objects.filter(
        appointment=appointment,
        type=AppointmentProposal.Type.REQUESTED_ALTERNATIVES,
        status=AppointmentProposal.Status.PENDING,
    ).exists()


def _is_inside_quiet_hours(value: time, *, start: time, end: time) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= value < end
    return value >= start or value < end


def schedule_default_reminders_for_partners(partners: Iterable[Partner]) -> int:
    """Utility for admin/setup paths that want to initialize defaults eagerly."""

    count = 0
    for partner in partners:
        get_or_create_notification_settings(partner=partner)
        count += len(ensure_default_reminder_rules(partner=partner))
    return count
