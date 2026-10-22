from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from core.database.models import PartnerBoundModel

ALLOWED_SLOT_STEPS = (5, 10, 15, 20, 30, 60)
DEFAULT_SLOT_STEP_MINUTES = 15
DEFAULT_BOOKING_HORIZON_DAYS = 30
DEFAULT_NEAR_FIT_TOLERANCE_MINUTES = 10
DEFAULT_CANCELLATION_DEADLINE_HOURS = 6
MAX_BOOKING_HORIZON_DAYS = 365
MAX_NEAR_FIT_TOLERANCE_MINUTES = 30
MAX_CANCELLATION_DEADLINE_HOURS = 168
MAX_AVAILABILITY_WINDOW_LENGTH = timedelta(hours=24)
MAX_BLOCKED_TIME_LENGTH = timedelta(days=31)
MAX_ADVISORY_LOCK_KEY = 2_147_483_647


def generate_advisory_lock_key() -> int:
    """Generate a positive int32 key for PostgreSQL two-argument advisory locks."""

    return secrets.randbelow(MAX_ADVISORY_LOCK_KEY) + 1


class BookingResource(PartnerBoundModel):
    """Calendar resource whose confirmed appointments must not overlap."""

    class ResourceType(models.TextChoices):
        SOLO_MASTER = "solo_master", "Solo master"

    name = models.CharField(max_length=255)
    type = models.CharField(
        max_length=32,
        choices=ResourceType.choices,
        default=ResourceType.SOLO_MASTER,
    )
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    advisory_lock_key = models.PositiveIntegerField(
        unique=True,
        editable=False,
        default=generate_advisory_lock_key,
    )

    class Meta:
        ordering = ["partner__name", "sort_order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["partner"],
                condition=models.Q(is_default=True),
                name="schedule_unique_default_booking_resource",
            ),
        ]
        indexes = [
            models.Index(fields=["partner", "is_active", "sort_order"]),
            models.Index(fields=["partner", "is_default"]),
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs) -> None:
        if not self._state.adding:
            original = type(self).objects.only("advisory_lock_key").get(pk=self.pk)
            if original.advisory_lock_key != self.advisory_lock_key:
                raise ValidationError({"advisory_lock_key": "Advisory lock key is immutable."})
        super().save(*args, **kwargs)


class PartnerScheduleSettings(PartnerBoundModel):
    """Partner-controlled booking behavior owned by the schedule domain."""

    class ConfirmationMode(models.TextChoices):
        AUTO = "auto", "Auto"
        MANUAL = "manual", "Manual"

    partner = models.OneToOneField(
        "partners.Partner",
        on_delete=models.CASCADE,
        related_name="schedule_settings",
    )
    confirmation_mode = models.CharField(
        max_length=16,
        choices=ConfirmationMode.choices,
        blank=True,
    )
    slot_step_minutes = models.PositiveIntegerField(default=DEFAULT_SLOT_STEP_MINUTES)
    booking_horizon_days = models.PositiveIntegerField(default=DEFAULT_BOOKING_HORIZON_DAYS)
    near_fit_tolerance_minutes = models.PositiveIntegerField(
        default=DEFAULT_NEAR_FIT_TOLERANCE_MINUTES,
    )
    cancellation_deadline_hours = models.PositiveIntegerField(
        default=DEFAULT_CANCELLATION_DEADLINE_HOURS,
    )
    require_phone_for_booking = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["partner", "confirmation_mode"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(slot_step_minutes__in=ALLOWED_SLOT_STEPS),
                name="schedule_settings_slot_step_allowed",
            ),
            models.CheckConstraint(
                condition=models.Q(booking_horizon_days__gte=1)
                & models.Q(booking_horizon_days__lte=MAX_BOOKING_HORIZON_DAYS),
                name="schedule_settings_booking_horizon_bounds",
            ),
            models.CheckConstraint(
                condition=models.Q(near_fit_tolerance_minutes__gte=0)
                & models.Q(near_fit_tolerance_minutes__lte=MAX_NEAR_FIT_TOLERANCE_MINUTES),
                name="schedule_settings_near_fit_bounds",
            ),
            models.CheckConstraint(
                condition=models.Q(cancellation_deadline_hours__gte=0)
                & models.Q(cancellation_deadline_hours__lte=MAX_CANCELLATION_DEADLINE_HOURS),
                name="schedule_settings_cancel_deadline_bounds",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.partner} schedule settings"


class AvailabilityTemplate(PartnerBoundModel):
    """Reusable weekly availability pattern that can be applied into real windows."""

    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["partner__name", "sort_order", "name"]
        indexes = [
            models.Index(fields=["partner", "is_active", "sort_order"]),
        ]

    def __str__(self) -> str:
        return self.name


class AvailabilityTemplateWindow(models.Model):
    """Weekly local-time window inside an availability template."""

    template = models.ForeignKey(
        "schedule.AvailabilityTemplate",
        on_delete=models.CASCADE,
        related_name="windows",
    )
    weekday = models.PositiveSmallIntegerField()
    start_time = models.TimeField()
    end_time = models.TimeField()

    class Meta:
        ordering = ["template__sort_order", "weekday", "start_time"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(weekday__gte=0) & models.Q(weekday__lte=6),
                name="schedule_template_window_weekday_bounds",
            ),
            models.CheckConstraint(
                condition=models.Q(start_time__lt=models.F("end_time")),
                name="schedule_template_window_start_before_end",
            ),
        ]
        indexes = [
            models.Index(fields=["template", "weekday", "start_time"]),
        ]

    def __str__(self) -> str:
        return f"{self.template} {self.weekday} {self.start_time}-{self.end_time}"


class AvailabilityWindow(PartnerBoundModel):
    """Concrete UTC interval where clients may see generated booking slots."""

    booking_resource = models.ForeignKey(
        "schedule.BookingResource",
        on_delete=models.CASCADE,
        related_name="availability_windows",
    )
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    source_template = models.ForeignKey(
        "schedule.AvailabilityTemplate",
        on_delete=models.SET_NULL,
        related_name="generated_windows",
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_availability_windows",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["start_at", "end_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(start_at__lt=models.F("end_at")),
                name="schedule_availability_start_before_end",
            ),
        ]
        indexes = [
            models.Index(fields=["partner", "start_at"]),
            models.Index(fields=["partner", "end_at"]),
            models.Index(fields=["booking_resource", "start_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.booking_resource} {self.start_at} - {self.end_at}"


class BlockedTime(PartnerBoundModel):
    """Concrete UTC interval that removes otherwise available booking slots."""

    booking_resource = models.ForeignKey(
        "schedule.BookingResource",
        on_delete=models.CASCADE,
        related_name="blocked_times",
    )
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    reason = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_blocked_times",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["start_at", "end_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(start_at__lt=models.F("end_at")),
                name="schedule_blocked_time_start_before_end",
            ),
        ]
        indexes = [
            models.Index(fields=["partner", "start_at"]),
            models.Index(fields=["partner", "end_at"]),
            models.Index(fields=["booking_resource", "start_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.booking_resource} blocked {self.start_at} - {self.end_at}"
