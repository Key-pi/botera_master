from __future__ import annotations

from datetime import time

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.database.models import PartnerBoundModel, RetainUntilModel, TimeStampedModel

DEFAULT_CLIENT_REMINDER_OFFSETS_MINUTES = (24 * 60, 2 * 60)
DEFAULT_MASTER_REMINDER_OFFSET_MINUTES = 45
MIN_CLIENT_REMINDER_OFFSET_MINUTES = 15
MAX_CLIENT_REMINDER_OFFSET_MINUTES = 30 * 24 * 60
MAX_CLIENT_REMINDER_RULES = 5
MIN_MASTER_REMINDER_OFFSET_MINUTES = 5
MAX_MASTER_REMINDER_OFFSET_MINUTES = 7 * 24 * 60
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
        verbose_name=_("partner"),
    )
    quiet_hours_enabled = models.BooleanField(_("quiet hours enabled"), default=True)
    quiet_hours_start_local = models.TimeField(
        _("quiet hours start"), default=DEFAULT_QUIET_HOURS_START_LOCAL
    )
    quiet_hours_end_local = models.TimeField(
        _("quiet hours end"), default=DEFAULT_QUIET_HOURS_END_LOCAL
    )
    morning_digest_enabled = models.BooleanField(_("morning digest enabled"), default=True)
    morning_digest_time_local = models.TimeField(
        _("morning digest time"), default=DEFAULT_MORNING_DIGEST_TIME_LOCAL
    )
    stale_requested_base_interval_minutes = models.PositiveIntegerField(
        _("stale request reminder interval in minutes"),
        default=DEFAULT_STALE_REQUESTED_INTERVAL_MINUTES,
    )

    class Meta:
        verbose_name = _("partner notification settings")
        verbose_name_plural = _("partner notification settings")
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
        CLIENT = "client", _("Client")
        MASTER = "master", _("Master")

    target = models.CharField(_("target"), max_length=16, choices=Target.choices)
    offset_minutes = models.PositiveIntegerField(_("offset in minutes"))
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("reminder rule")
        verbose_name_plural = _("reminder rules")
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
            _("Client appointment created"),
        )
        CLIENT_BOOKING_REQUEST_CONFIRMED = (
            "client_booking_request_confirmed",
            _("Client booking request confirmed"),
        )
        CLIENT_BOOKING_REQUEST_DECLINED = (
            "client_booking_request_declined",
            _("Client booking request declined"),
        )
        CLIENT_APPOINTMENT_CANCELLED_BY_MASTER = (
            "client_appointment_cancelled_by_master",
            _("Client appointment cancelled by master"),
        )
        CLIENT_APPOINTMENT_COMPLETED = (
            "client_appointment_completed",
            _("Client appointment completed"),
        )
        CLIENT_APPOINTMENT_NO_SHOW = (
            "client_appointment_no_show",
            _("Client appointment no-show"),
        )
        CLIENT_PENALTY_CREATED = "client_penalty_created", _("Client penalty created")
        CLIENT_PENALTY_PAID = "client_penalty_paid", _("Client penalty paid")
        CLIENT_PENALTY_WAIVED = "client_penalty_waived", _("Client penalty waived")
        CLIENT_APPOINTMENT_REMINDER = (
            "client_appointment_reminder",
            _("Client appointment reminder"),
        )
        CLIENT_SCHEDULE_CONFLICT_RESCHEDULE_REQUEST = (
            "client_schedule_conflict_reschedule_request",
            _("Client schedule-conflict reschedule request"),
        )
        MASTER_BOOKING_REQUEST = "master_booking_request", _("Master booking request")
        MASTER_CLIENT_BOOKED_APPOINTMENT = (
            "master_client_booked_appointment",
            _("Master client-booked appointment"),
        )
        MASTER_APPOINTMENT_REMINDER = (
            "master_appointment_reminder",
            _("Master appointment reminder"),
        )
        MASTER_CLIENT_CANCELLED_APPOINTMENT = (
            "master_client_cancelled_appointment",
            _("Master client-cancelled appointment"),
        )
        MASTER_CLIENT_REQUESTED_RESCHEDULE = (
            "master_client_requested_reschedule",
            _("Master client-requested reschedule"),
        )
        CLIENT_APPOINTMENT_PROPOSAL = (
            "client_appointment_proposal",
            _("Client appointment proposal"),
        )
        CLIENT_APPOINTMENT_PROPOSAL_ACCEPTED = (
            "client_appointment_proposal_accepted",
            _("Client appointment proposal accepted"),
        )
        CLIENT_APPOINTMENT_PROPOSAL_DECLINED = (
            "client_appointment_proposal_declined",
            _("Client appointment proposal declined"),
        )
        CLIENT_APPOINTMENT_PROPOSAL_EXPIRED = (
            "client_appointment_proposal_expired",
            _("Client appointment proposal expired"),
        )
        MASTER_CLIENT_ACCEPTED_PROPOSAL = (
            "master_client_accepted_proposal",
            _("Master client-accepted proposal"),
        )
        MASTER_CLIENT_DECLINED_PROPOSAL = (
            "master_client_declined_proposal",
            _("Master client-declined proposal"),
        )
        MASTER_APPOINTMENT_PROPOSAL_EXPIRED = (
            "master_appointment_proposal_expired",
            _("Master appointment proposal expired"),
        )
        MASTER_MORNING_DIGEST = "master_morning_digest", _("Master morning digest")
        STALE_REQUESTED = "stale_requested", _("Stale requested appointment")
        FAILED_DELIVERY_TO_MASTER = "failed_delivery_to_master", _("Failed delivery to master")

    class Status(models.TextChoices):
        QUEUED = "queued", _("Queued")
        SENDING = "sending", _("Sending")
        SENT = "sent", _("Sent")
        FAILED = "failed", _("Failed")
        SKIPPED = "skipped", _("Skipped")
        CANCELLED = "cancelled", _("Cancelled")
        STALE = "stale", _("Stale")

    appointment = models.ForeignKey(
        "appointments.Appointment",
        on_delete=models.CASCADE,
        related_name="scheduled_notifications",
        null=True,
        blank=True,
        verbose_name=_("appointment"),
    )
    appointment_proposal = models.ForeignKey(
        "appointments.AppointmentProposal",
        on_delete=models.CASCADE,
        related_name="scheduled_notifications",
        null=True,
        blank=True,
        verbose_name=_("appointment proposal"),
    )
    client_profile = models.ForeignKey(
        "clients.ClientProfile",
        on_delete=models.SET_NULL,
        related_name="scheduled_notifications",
        null=True,
        blank=True,
        verbose_name=_("client profile"),
    )
    penalty = models.ForeignKey(
        "finance.Penalty",
        on_delete=models.CASCADE,
        related_name="scheduled_notifications",
        null=True,
        blank=True,
        verbose_name=_("penalty"),
    )
    reminder_rule = models.ForeignKey(
        "notifications.ReminderRule",
        on_delete=models.SET_NULL,
        related_name="scheduled_notifications",
        null=True,
        blank=True,
        verbose_name=_("reminder rule"),
    )
    source_notification = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        related_name="failure_markers",
        null=True,
        blank=True,
        verbose_name=_("source notification"),
    )
    type = models.CharField(_("type"), max_length=64, choices=Type.choices)
    due_at = models.DateTimeField(_("due at"), db_index=True)
    status = models.CharField(
        _("status"), max_length=16, choices=Status.choices, default=Status.QUEUED
    )
    idempotency_key = models.CharField(_("idempotency key"), max_length=255, unique=True)
    attempt_count = models.PositiveSmallIntegerField(_("attempt count"), default=0)
    max_attempts = models.PositiveSmallIntegerField(
        _("maximum attempts"), default=DEFAULT_NOTIFICATION_MAX_ATTEMPTS
    )
    stale_sequence = models.PositiveSmallIntegerField(_("stale sequence"), default=0)
    last_failure_code = models.CharField(_("last failure code"), max_length=128, blank=True)
    last_failure_message_masked = models.TextField(_("masked last failure message"), blank=True)
    failed_visible_to_partner = models.BooleanField(_("failure visible to partner"), default=False)
    sent_at = models.DateTimeField(_("sent at"), null=True, blank=True)

    class Meta:
        verbose_name = _("scheduled notification")
        verbose_name_plural = _("scheduled notifications")
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
        SENT = "sent", _("Sent")
        FAILED = "failed", _("Failed")
        SKIPPED = "skipped", _("Skipped")

    notification = models.ForeignKey(
        "notifications.ScheduledNotification",
        on_delete=models.CASCADE,
        related_name="delivery_attempts",
        null=True,
        blank=True,
        verbose_name=_("notification"),
    )
    broadcast_recipient = models.ForeignKey(
        "broadcasts.BroadcastRecipient",
        on_delete=models.CASCADE,
        related_name="delivery_attempts",
        null=True,
        blank=True,
        verbose_name=_("broadcast recipient"),
    )
    conversation_message = models.ForeignKey(
        "messaging.ConversationMessage",
        on_delete=models.CASCADE,
        related_name="delivery_attempts",
        null=True,
        blank=True,
        verbose_name=_("conversation message"),
    )
    channel = models.CharField(
        _("channel"),
        max_length=32,
        choices=Channel.choices,
        default=Channel.TELEGRAM,
    )
    status = models.CharField(_("status"), max_length=16, choices=Status.choices)
    failure_code = models.CharField(_("failure code"), max_length=128, blank=True)
    failure_message_masked = models.TextField(_("masked failure message"), blank=True)
    message_preview_masked = models.TextField(_("masked message preview"), blank=True)
    attempted_at = models.DateTimeField(_("attempted at"), default=timezone.now)

    class Meta:
        verbose_name = _("delivery attempt")
        verbose_name_plural = _("delivery attempts")
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
