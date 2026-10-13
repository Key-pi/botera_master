from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.catalog.models import DEFAULT_CURRENCY
from core.database.models import PartnerBoundModel, TimeStampedModel

DEFAULT_LATE_CANCEL_PENALTY_AMOUNT = 5000
DEFAULT_NO_SHOW_PENALTY_AMOUNT = 5000
DEFAULT_LATE_ARRIVAL_THRESHOLD_MINUTES = 15
DEFAULT_LATE_ARRIVAL_PENALTY_AMOUNT = 2000


class PartnerPenaltySettings(TimeStampedModel):
    """Partner-controlled penalty behavior and default amounts."""

    partner = models.OneToOneField(
        "partners.Partner",
        on_delete=models.CASCADE,
        related_name="penalty_settings",
    )
    penalties_enabled = models.BooleanField(default=False)
    late_cancel_penalty_amount = models.PositiveIntegerField(
        default=DEFAULT_LATE_CANCEL_PENALTY_AMOUNT,
    )
    no_show_penalty_amount = models.PositiveIntegerField(default=DEFAULT_NO_SHOW_PENALTY_AMOUNT)
    late_arrival_threshold_minutes = models.PositiveIntegerField(
        default=DEFAULT_LATE_ARRIVAL_THRESHOLD_MINUTES,
    )
    late_arrival_penalty_amount = models.PositiveIntegerField(
        default=DEFAULT_LATE_ARRIVAL_PENALTY_AMOUNT,
    )
    currency = models.CharField(max_length=3, default=DEFAULT_CURRENCY)

    class Meta:
        ordering = ["partner__name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(late_cancel_penalty_amount__gte=0),
                name="finance_late_cancel_penalty_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(no_show_penalty_amount__gte=0),
                name="finance_no_show_penalty_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(late_arrival_threshold_minutes__gt=0),
                name="finance_late_arrival_threshold_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(late_arrival_penalty_amount__gte=0),
                name="finance_late_arrival_penalty_non_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.partner} penalty settings"


class Penalty(PartnerBoundModel):
    """Partner-scoped monetary penalty/debt attached to one client."""

    class Reason(models.TextChoices):
        LATE_CANCEL = "late_cancel", "Late cancellation"
        NO_SHOW = "no_show", "No-show"
        LATE_ARRIVAL = "late_arrival", "Late arrival"

    class Status(models.TextChoices):
        UNPAID = "unpaid", "Unpaid"
        APPLIED_TO_APPOINTMENT = "applied_to_appointment", "Applied to appointment"
        PAID = "paid", "Paid"
        WAIVED = "waived", "Waived"

    client_profile = models.ForeignKey(
        "clients.ClientProfile",
        on_delete=models.PROTECT,
        related_name="penalties",
    )
    source_appointment = models.ForeignKey(
        "appointments.Appointment",
        on_delete=models.SET_NULL,
        related_name="source_penalties",
        null=True,
        blank=True,
    )
    reason = models.CharField(max_length=32, choices=Reason.choices)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.UNPAID)
    amount = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default=DEFAULT_CURRENCY)
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_penalties",
        null=True,
        blank=True,
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    waived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gte=0),
                name="finance_penalty_amount_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["partner", "status", "created_at"]),
            models.Index(fields=["client_profile", "status"]),
            models.Index(fields=["source_appointment", "reason"]),
        ]

    def __str__(self) -> str:
        return f"{self.client_profile} {self.reason} {self.amount} {self.currency}"

    def clean(self) -> None:
        super().clean()
        if (
            self.client_profile_id
            and self.partner_id
            and self.client_profile.partner_id != self.partner_id
        ):
            raise ValidationError(
                {"client_profile": "Penalty client must belong to the same partner."}
            )
        if (
            self.source_appointment_id
            and self.partner_id
            and self.source_appointment.partner_id != self.partner_id
        ):
            raise ValidationError(
                {"source_appointment": "Source appointment must belong to the same partner."}
            )
        if (
            self.source_appointment_id
            and self.client_profile_id
            and self.source_appointment.client_profile_id != self.client_profile_id
        ):
            raise ValidationError(
                {"source_appointment": "Source appointment must belong to this client."}
            )


class PenaltyApplication(PartnerBoundModel):
    """Historical application of a penalty amount to an appointment total."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        RELEASED = "released", "Released"
        SETTLED = "settled", "Settled"

    penalty = models.ForeignKey(
        "finance.Penalty",
        on_delete=models.PROTECT,
        related_name="applications",
    )
    appointment = models.ForeignKey(
        "appointments.Appointment",
        on_delete=models.PROTECT,
        related_name="penalty_applications",
    )
    amount_snapshot = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default=DEFAULT_CURRENCY)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    released_at = models.DateTimeField(null=True, blank=True)
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount_snapshot__gte=0),
                name="finance_penalty_application_amount_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["partner", "status", "created_at"]),
            models.Index(fields=["penalty", "status"]),
            models.Index(fields=["appointment", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.penalty_id} -> {self.appointment_id} ({self.status})"

    def clean(self) -> None:
        super().clean()
        if self.penalty_id and self.partner_id and self.penalty.partner_id != self.partner_id:
            raise ValidationError({"penalty": "Penalty must belong to the same partner."})
        if (
            self.appointment_id
            and self.partner_id
            and self.appointment.partner_id != self.partner_id
        ):
            raise ValidationError(
                {"appointment": "Appointment must belong to the same partner."}
            )
        if (
            self.penalty_id
            and self.appointment_id
            and self.penalty.client_profile_id != self.appointment.client_profile_id
        ):
            raise ValidationError(
                {"appointment": "Penalty and appointment must belong to one client."}
            )


class Expense(PartnerBoundModel):
    """Manual partner expense used in lightweight finance reports."""

    date = models.DateField()
    amount = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default=DEFAULT_CURRENCY)
    description = models.CharField(max_length=255)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_expenses",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-date", "-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="finance_expense_amount_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["partner", "date"]),
        ]

    def __str__(self) -> str:
        return f"{self.date} {self.amount} {self.currency}"
