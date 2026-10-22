from __future__ import annotations

from datetime import time

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.database.models import PartnerBoundModel, RetainUntilModel, TimeStampedModel

DEFAULT_CLIENT_REMINDER_OFFSETS_MINUTES = (24 * 60, 2 * 60)
DEFAULT_MASTER_REMINDER_OFFSET_MINUTES = 45
DEFAULT_MORNING_DIGEST_TIME_LOCAL = time(8, 0)
DEFAULT_QUIET_HOURS_START_LOCAL = time(22, 0)
DEFAULT_QUIET_HOURS_END_LOCAL = time(8, 0)
DEFAULT_STALE_REQUESTED_INTERVAL_MINUTES = 3 * 60
MIN_STALE_REQUESTED_INTERVAL_MINUTES = 10
MAX_STALE_REQUESTED_INTERVAL_MINUTES = 72 * 60
MIN_STALE_REQUESTED_REPEAT_MINUTES = 5
DEFAULT_NOTIFICATION_MAX_ATTEMPTS = 3
NOTIFICATION_RETAIN_DAYS = 90


class PartnerNotificationSettings(TimeStampedModel):
    """Partner-owned notification timing and quiet-hours settings."""

    partner = models.OneToOneField(
        "partners.Partner",
        on_delete=models.CASCADE,
        related_name="notification_settings",
    )
    quiet_hours_enabled = models.BooleanField(default=True)
    quiet_hours_start_local = models.TimeField(default=DEFAULT_QUIET_HOURS_START_LOCAL)
    quiet_hours_end_local = models.TimeField(default=DEFAULT_QUIET_HOURS_END_LOCAL)
    morning_digest_enabled = models.BooleanField(default=True)
    morning_digest_time_local = models.TimeField(default=DEFAULT_MORNING_DIGEST_TIME_LOCAL)
    stale_requested_base_interval_minutes = models.PositiveIntegerField(
        default=DEFAULT_STALE_REQUESTED_INTERVAL_MINUTES,
    )

    class Meta:
        ordering = ["partner__name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    stale_requested_base_interval_minutes__gte=MIN_STALE_REQUESTED_INTERVAL_MINUTES
                )
                & models.Q(
                    stale_requested_base_interval_minutes__lte=MAX_STALE_REQUESTED_INTERVAL_MINUTES
                ),
                name="notifications_stale_requested_interval_bounds",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.partner} notification settings"


class ReminderRule(PartnerBoundModel):
    """Partner-configured appointment reminder rule."""

    class Target(models.TextChoices):
        CLIENT = "client", "Client"
        MASTER = "master", "Master"

    target = models.CharField(max_length=16, choices=Target.choices)
    offset_minutes = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["partner__name", "target", "-offset_minutes"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(offset_minutes__gt=0),
                name="notifications_reminder_offset_positive",
            ),
            models.UniqueConstraint(
                fields=["partner", "target", "offset_minutes"],
                name="notifications_unique_reminder_rule_offset",
            ),
        ]
        indexes = [
            models.Index(fields=["partner", "target", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.partner} {self.target} {self.offset_minutes}m"


class ScheduledNotification(PartnerBoundModel, RetainUntilModel):
    """Durable scheduled reminder/notification job state."""

    class Type(models.TextChoices):
        CLIENT_APPOINTMENT_CREATED = (
            "client_appointment_created",
            "Client appointment created",
        )
        CLIENT_BOOKING_REQUEST_CONFIRMED = (
            "client_booking_request_confirmed",
            "Client booking request confirmed",
        )
        CLIENT_BOOKING_REQUEST_DECLINED = (
            "client_booking_request_declined",
            "Client booking request declined",
        )
        CLIENT_APPOINTMENT_CANCELLED_BY_MASTER = (
            "client_appointment_cancelled_by_master",
            "Client appointment cancelled by master",
        )
        CLIENT_APPOINTMENT_COMPLETED = (
            "client_appointment_completed",
            "Client appointment completed",
        )
        CLIENT_APPOINTMENT_NO_SHOW = (
            "client_appointment_no_show",
            "Client appointment no-show",
        )
        CLIENT_PENALTY_CREATED = "client_penalty_created", "Client penalty created"
        CLIENT_PENALTY_PAID = "client_penalty_paid", "Client penalty paid"
        CLIENT_PENALTY_WAIVED = "client_penalty_waived", "Client penalty waived"
        CLIENT_APPOINTMENT_REMINDER = (
            "client_appointment_reminder",
            "Client appointment reminder",
        )
        MASTER_BOOKING_REQUEST = "master_booking_request", "Master booking request"
        MASTER_CLIENT_BOOKED_APPOINTMENT = (
            "master_client_booked_appointment",
            "Master client-booked appointment",
        )
        MASTER_APPOINTMENT_REMINDER = (
            "master_appointment_reminder",
            "Master appointment reminder",
        )
        MASTER_CLIENT_CANCELLED_APPOINTMENT = (
            "master_client_cancelled_appointment",
            "Master client-cancelled appointment",
        )
        MASTER_CLIENT_REQUESTED_RESCHEDULE = (
            "master_client_requested_reschedule",
            "Master client-requested reschedule",
        )
        CLIENT_APPOINTMENT_PROPOSAL = (
            "client_appointment_proposal",
            "Client appointment proposal",
        )
        CLIENT_APPOINTMENT_PROPOSAL_ACCEPTED = (
            "client_appointment_proposal_accepted",
            "Client appointment proposal accepted",
        )
        CLIENT_APPOINTMENT_PROPOSAL_DECLINED = (
            "client_appointment_proposal_declined",
            "Client appointment proposal declined",
        )
        CLIENT_APPOINTMENT_PROPOSAL_EXPIRED = (
            "client_appointment_proposal_expired",
            "Client appointment proposal expired",
        )
        MASTER_CLIENT_ACCEPTED_PROPOSAL = (
            "master_client_accepted_proposal",
            "Master client-accepted proposal",
        )
        MASTER_CLIENT_DECLINED_PROPOSAL = (
            "master_client_declined_proposal",
            "Master client-declined proposal",
        )
        MASTER_APPOINTMENT_PROPOSAL_EXPIRED = (
            "master_appointment_proposal_expired",
            "Master appointment proposal expired",
        )
        MASTER_MORNING_DIGEST = "master_morning_digest", "Master morning digest"
        STALE_REQUESTED = "stale_requested", "Stale requested appointment"
        FAILED_DELIVERY_TO_MASTER = "failed_delivery_to_master", "Failed delivery to master"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"
        CANCELLED = "cancelled", "Cancelled"
        STALE = "stale", "Stale"

    appointment = models.ForeignKey(
        "appointments.Appointment",
        on_delete=models.CASCADE,
        related_name="scheduled_notifications",
        null=True,
        blank=True,
    )
    appointment_proposal = models.ForeignKey(
        "appointments.AppointmentProposal",
        on_delete=models.CASCADE,
        related_name="scheduled_notifications",
        null=True,
        blank=True,
    )
    client_profile = models.ForeignKey(
        "clients.ClientProfile",
        on_delete=models.SET_NULL,
        related_name="scheduled_notifications",
        null=True,
        blank=True,
    )
    penalty = models.ForeignKey(
        "finance.Penalty",
        on_delete=models.CASCADE,
        related_name="scheduled_notifications",
        null=True,
        blank=True,
    )
    reminder_rule = models.ForeignKey(
        "notifications.ReminderRule",
        on_delete=models.SET_NULL,
        related_name="scheduled_notifications",
        null=True,
        blank=True,
    )
    source_notification = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        related_name="failure_markers",
        null=True,
        blank=True,
    )
    type = models.CharField(max_length=64, choices=Type.choices)
    due_at = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    idempotency_key = models.CharField(max_length=255, unique=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=DEFAULT_NOTIFICATION_MAX_ATTEMPTS)
    stale_sequence = models.PositiveSmallIntegerField(default=0)
    last_failure_code = models.CharField(max_length=128, blank=True)
    last_failure_message_masked = models.TextField(blank=True)
    failed_visible_to_partner = models.BooleanField(default=False)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["due_at", "created_at"]
        indexes = [
            models.Index(fields=["status", "due_at"]),
            models.Index(fields=["partner", "status", "due_at"]),
            models.Index(fields=["appointment", "status"]),
            models.Index(fields=["appointment_proposal", "status"]),
            models.Index(fields=["client_profile", "status"]),
            models.Index(fields=["penalty", "status"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        type="failed_delivery_to_master",
                        source_notification__isnull=False,
                    )
                    | ~models.Q(type="failed_delivery_to_master")
                    & models.Q(source_notification__isnull=True)
                ),
                name="notifications_failure_marker_has_source",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        type__in=(
                            "client_penalty_created",
                            "client_penalty_paid",
                            "client_penalty_waived",
                        ),
                        penalty__isnull=False,
                    )
                    | ~models.Q(
                        type__in=(
                            "client_penalty_created",
                            "client_penalty_paid",
                            "client_penalty_waived",
                        )
                    )
                    & models.Q(penalty__isnull=True)
                ),
                name="notifications_penalty_notice_has_penalty",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.type}:{self.status}:{self.due_at}"

    def clean(self) -> None:
        super().clean()
        if (
            self.source_notification_id
            and self.partner_id
            and self.source_notification.partner_id != self.partner_id
        ):
            raise ValidationError(
                {"source_notification": "Source notification must belong to the same partner."}
            )
        if self.penalty_id and self.partner_id and self.penalty.partner_id != self.partner_id:
            raise ValidationError({"penalty": "Penalty must belong to the same partner."})
        if (
            self.penalty_id
            and self.client_profile_id
            and self.penalty.client_profile_id != self.client_profile_id
        ):
            raise ValidationError(
                {"client_profile": "Penalty notification must target the penalty client."}
            )


class DeliveryAttempt(PartnerBoundModel, RetainUntilModel):
    """Low-level delivery attempt record for notification diagnostics."""

    class Channel(models.TextChoices):
        TELEGRAM = "telegram", "Telegram"

    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    notification = models.ForeignKey(
        "notifications.ScheduledNotification",
        on_delete=models.CASCADE,
        related_name="delivery_attempts",
        null=True,
        blank=True,
    )
    broadcast_recipient = models.ForeignKey(
        "broadcasts.BroadcastRecipient",
        on_delete=models.CASCADE,
        related_name="delivery_attempts",
        null=True,
        blank=True,
    )
    conversation_message = models.ForeignKey(
        "messaging.ConversationMessage",
        on_delete=models.CASCADE,
        related_name="delivery_attempts",
        null=True,
        blank=True,
    )
    channel = models.CharField(
        max_length=32,
        choices=Channel.choices,
        default=Channel.TELEGRAM,
    )
    status = models.CharField(max_length=16, choices=Status.choices)
    failure_code = models.CharField(max_length=128, blank=True)
    failure_message_masked = models.TextField(blank=True)
    message_preview_masked = models.TextField(blank=True)
    attempted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-attempted_at", "-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    (
                        models.Q(notification__isnull=False)
                        & models.Q(broadcast_recipient__isnull=True)
                        & models.Q(conversation_message__isnull=True)
                    )
                    | (
                        models.Q(notification__isnull=True)
                        & models.Q(broadcast_recipient__isnull=False)
                        & models.Q(conversation_message__isnull=True)
                    )
                    | (
                        models.Q(notification__isnull=True)
                        & models.Q(broadcast_recipient__isnull=True)
                        & models.Q(conversation_message__isnull=False)
                    )
                ),
                name="notifications_delivery_attempt_one_source",
            ),
        ]
        indexes = [
            models.Index(fields=["notification", "status"]),
            models.Index(fields=["broadcast_recipient", "status"]),
            models.Index(fields=["conversation_message", "status"]),
            models.Index(fields=["partner", "attempted_at"]),
        ]

    def __str__(self) -> str:
        source = self.notification_id or self.broadcast_recipient_id or self.conversation_message_id
        return f"{source}:{self.status}:{self.attempted_at}"
