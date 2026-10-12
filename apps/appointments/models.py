from __future__ import annotations

from django.conf import settings
from django.db import models

from apps.catalog.models import DEFAULT_CURRENCY
from core.database.models import PartnerBoundModel


class Appointment(PartnerBoundModel):
    """Client visit/request with immutable service snapshots."""

    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        CONFIRMED = "confirmed", "Confirmed"
        CANCELLED_BY_CLIENT = "cancelled_by_client", "Cancelled by client"
        CANCELLED_BY_MASTER = "cancelled_by_master", "Cancelled by master"
        COMPLETED = "completed", "Completed"
        NO_SHOW = "no_show", "No-show"

    class Source(models.TextChoices):
        CLIENT_BOT = "client_bot", "Client bot"
        PARTNER_ADMIN = "partner_admin", "Partner admin"
        PROPOSAL = "proposal", "Proposal"

    booking_resource = models.ForeignKey(
        "schedule.BookingResource",
        on_delete=models.PROTECT,
        related_name="appointments",
    )
    client_profile = models.ForeignKey(
        "clients.ClientProfile",
        on_delete=models.PROTECT,
        related_name="appointments",
    )
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    status = models.CharField(max_length=32, choices=Status.choices)
    source = models.CharField(max_length=32, choices=Source.choices, default=Source.CLIENT_BOT)
    confirmation_mode_snapshot = models.CharField(max_length=16, blank=True)
    is_near_fit = models.BooleanField(default=False)
    client_comment = models.TextField(blank=True)
    private_master_note = models.TextField(blank=True)
    client_visible_reason = models.TextField(blank=True)
    service_total_amount = models.PositiveIntegerField(default=0)
    penalties_total_amount = models.PositiveIntegerField(default=0)
    total_amount = models.PositiveIntegerField(default=0)
    currency = models.CharField(max_length=3, default=DEFAULT_CURRENCY)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_appointments",
        null=True,
        blank=True,
    )
    requested_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    no_show_at = models.DateTimeField(null=True, blank=True)
    status_changed_at = models.DateTimeField(null=True, blank=True)
    last_correction_reason = models.TextField(blank=True)
    last_correction_at = models.DateTimeField(null=True, blank=True)
    last_correction_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="corrected_appointments",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["start_at", "created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(start_at__lt=models.F("end_at")),
                name="appointments_start_before_end",
            ),
            models.CheckConstraint(
                condition=models.Q(service_total_amount__gte=0),
                name="appointments_service_total_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(penalties_total_amount__gte=0),
                name="appointments_penalties_total_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(total_amount__gte=0),
                name="appointments_total_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["partner", "status", "start_at"]),
            models.Index(fields=["booking_resource", "status", "start_at"]),
            models.Index(fields=["client_profile", "start_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.client_profile} {self.start_at} ({self.status})"

    @property
    def is_final(self) -> bool:
        return self.status in FINAL_APPOINTMENT_STATUSES


class AppointmentServiceItem(models.Model):
    """Snapshotted service line inside an appointment."""

    appointment = models.ForeignKey(
        "appointments.Appointment",
        on_delete=models.CASCADE,
        related_name="service_items",
    )
    service = models.ForeignKey(
        "catalog.Service",
        on_delete=models.SET_NULL,
        related_name="appointment_items",
        null=True,
        blank=True,
    )
    category_name_snapshot = models.CharField(max_length=255, blank=True)
    service_name_snapshot = models.CharField(max_length=255)
    description_snapshot = models.TextField(blank=True)
    price_amount = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default=DEFAULT_CURRENCY)
    duration_minutes = models.PositiveIntegerField()
    is_custom = models.BooleanField(default=False)
    is_overridden = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(price_amount__gte=0),
                name="appointment_item_price_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(duration_minutes__gt=0),
                name="appointment_item_duration_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["appointment", "sort_order"]),
            models.Index(fields=["service"]),
        ]

    def __str__(self) -> str:
        return self.service_name_snapshot


FINAL_APPOINTMENT_STATUSES = (
    Appointment.Status.CANCELLED_BY_CLIENT,
    Appointment.Status.CANCELLED_BY_MASTER,
    Appointment.Status.COMPLETED,
    Appointment.Status.NO_SHOW,
)

BLOCKING_APPOINTMENT_STATUSES = (Appointment.Status.CONFIRMED,)
