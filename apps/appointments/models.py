from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import DEFAULT_CURRENCY
from core.database.models import PartnerBoundModel


class Appointment(PartnerBoundModel):
    """Client visit/request with immutable service snapshots."""

    class Status(models.TextChoices):
        REQUESTED = "requested", _("Requested")
        CONFIRMED = "confirmed", _("Confirmed")
        CANCELLED_BY_CLIENT = "cancelled_by_client", _("Cancelled by client")
        CANCELLED_BY_MASTER = "cancelled_by_master", _("Cancelled by master")
        COMPLETED = "completed", _("Completed")
        NO_SHOW = "no_show", _("No-show")

    class Source(models.TextChoices):
        CLIENT_BOT = "client_bot", _("Client bot")
        PARTNER_ADMIN = "partner_admin", _("Partner admin")
        PROPOSAL = "proposal", _("Proposal")

    booking_resource = models.ForeignKey(
        "schedule.BookingResource",
        on_delete=models.PROTECT,
        related_name="appointments",
        verbose_name=_("booking resource"),
    )
    client_profile = models.ForeignKey(
        "clients.ClientProfile",
        on_delete=models.PROTECT,
        related_name="appointments",
        verbose_name=_("client profile"),
    )
    start_at = models.DateTimeField(_("start at"))
    end_at = models.DateTimeField(_("end at"))
    status = models.CharField(_("status"), max_length=32, choices=Status.choices)
    source = models.CharField(
        _("source"), max_length=32, choices=Source.choices, default=Source.CLIENT_BOT
    )
    confirmation_mode_snapshot = models.CharField(
        _("confirmation mode snapshot"), max_length=16, blank=True
    )
    is_near_fit = models.BooleanField(_("near-fit"), default=False)
    client_comment = models.TextField(_("client comment"), blank=True)
    private_master_note = models.TextField(_("private master note"), blank=True)
    client_visible_reason = models.TextField(_("client-visible reason"), blank=True)
    service_total_amount = models.PositiveIntegerField(_("services total"), default=0)
    penalties_total_amount = models.PositiveIntegerField(_("penalties total"), default=0)
    total_amount = models.PositiveIntegerField(_("total amount"), default=0)
    currency = models.CharField(_("currency"), max_length=3, default=DEFAULT_CURRENCY)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_appointments",
        null=True,
        blank=True,
        verbose_name=_("created by"),
    )
    requested_at = models.DateTimeField(_("requested at"), null=True, blank=True)
    confirmed_at = models.DateTimeField(_("confirmed at"), null=True, blank=True)
    cancelled_at = models.DateTimeField(_("cancelled at"), null=True, blank=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)
    no_show_at = models.DateTimeField(_("no-show at"), null=True, blank=True)
    status_changed_at = models.DateTimeField(_("status changed at"), null=True, blank=True)
    last_correction_reason = models.TextField(_("last correction reason"), blank=True)
    last_correction_at = models.DateTimeField(_("last correction at"), null=True, blank=True)
    last_correction_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="corrected_appointments",
        null=True,
        blank=True,
        verbose_name=_("last corrected by"),
    )

    class Meta:
        verbose_name = _("appointment")
        verbose_name_plural = _("appointments")
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
        verbose_name=_("appointment"),
    )
    service = models.ForeignKey(
        "catalog.Service",
        on_delete=models.SET_NULL,
        related_name="appointment_items",
        null=True,
        blank=True,
        verbose_name=_("service"),
    )
    category_name_snapshot = models.CharField(
        _("category name snapshot"), max_length=255, blank=True
    )
    service_name_snapshot = models.CharField(_("service name snapshot"), max_length=255)
    description_snapshot = models.TextField(_("description snapshot"), blank=True)
    price_amount = models.PositiveIntegerField(_("price amount"))
    currency = models.CharField(_("currency"), max_length=3, default=DEFAULT_CURRENCY)
    duration_minutes = models.PositiveIntegerField(_("duration in minutes"))
    is_custom = models.BooleanField(_("custom service"), default=False)
    is_overridden = models.BooleanField(_("overridden"), default=False)
    sort_order = models.PositiveIntegerField(_("sort order"), default=0)

    class Meta:
        verbose_name = _("appointment service item")
        verbose_name_plural = _("appointment service items")
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


class AppointmentProposal(PartnerBoundModel):
    """Client-visible proposal for changing or confirming an appointment variant."""

    class Type(models.TextChoices):
        RESCHEDULE_CONFIRMED = "reschedule_confirmed", _("Reschedule confirmed")
        REQUESTED_ALTERNATIVES = "requested_alternatives", _("Requested alternatives")
        APPOINTMENT_CHANGE = "appointment_change", _("Appointment change")

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        ACCEPTED = "accepted", _("Accepted")
        DECLINED = "declined", _("Declined")
        EXPIRED = "expired", _("Expired")
        CANCELLED = "cancelled", _("Cancelled")
        SUPERSEDED = "superseded", _("Superseded")

    appointment = models.ForeignKey(
        "appointments.Appointment",
        on_delete=models.CASCADE,
        related_name="proposals",
        verbose_name=_("appointment"),
    )
    type = models.CharField(_("type"), max_length=32, choices=Type.choices)
    status = models.CharField(
        _("status"),
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_appointment_proposals",
        null=True,
        blank=True,
        verbose_name=_("created by"),
    )
    expires_at = models.DateTimeField(_("expires at"), db_index=True)
    message = models.TextField(_("message"), blank=True)
    reason = models.TextField(_("reason"), blank=True)
    responded_at = models.DateTimeField(_("responded at"), null=True, blank=True)
    selected_option = models.ForeignKey(
        "appointments.AppointmentProposalOption",
        on_delete=models.SET_NULL,
        related_name="selected_for_proposals",
        null=True,
        blank=True,
        verbose_name=_("selected option"),
    )

    class Meta:
        verbose_name = _("appointment proposal")
        verbose_name_plural = _("appointment proposals")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["partner", "status", "expires_at"]),
            models.Index(fields=["appointment", "status"]),
            models.Index(fields=["type", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.appointment_id} {self.type} ({self.status})"

    def clean(self) -> None:
        super().clean()
        if (
            self.appointment_id
            and self.partner_id
            and self.appointment.partner_id != self.partner_id
        ):
            raise ValidationError(
                {"appointment": "Proposal appointment must belong to the same partner."}
            )
        if self.selected_option_id and self.id and self.selected_option.proposal_id != self.id:
            raise ValidationError(
                {"selected_option": "Selected option must belong to this proposal."}
            )


class AppointmentProposalOption(models.Model):
    """One selectable proposed variant inside an appointment proposal."""

    proposal = models.ForeignKey(
        "appointments.AppointmentProposal",
        on_delete=models.CASCADE,
        related_name="options",
        verbose_name=_("proposal"),
    )
    start_at = models.DateTimeField(_("start at"), null=True, blank=True)
    end_at = models.DateTimeField(_("end at"), null=True, blank=True)
    total_duration_minutes = models.PositiveIntegerField(_("total duration in minutes"))
    service_total_amount = models.PositiveIntegerField(_("services total"), default=0)
    penalties_total_amount = models.PositiveIntegerField(_("penalties total"), default=0)
    total_amount = models.PositiveIntegerField(_("total amount"), default=0)
    currency = models.CharField(_("currency"), max_length=3, default=DEFAULT_CURRENCY)
    is_near_fit = models.BooleanField(_("near-fit"), default=False)
    sort_order = models.PositiveIntegerField(_("sort order"), default=0)
    selected_at = models.DateTimeField(_("selected at"), null=True, blank=True)

    class Meta:
        verbose_name = _("appointment proposal option")
        verbose_name_plural = _("appointment proposal options")
        ordering = ["sort_order", "id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(start_at__isnull=True, end_at__isnull=True)
                    | models.Q(
                        start_at__isnull=False,
                        end_at__isnull=False,
                        start_at__lt=models.F("end_at"),
                    )
                ),
                name="proposal_option_start_before_end_or_empty",
            ),
            models.CheckConstraint(
                condition=models.Q(total_duration_minutes__gt=0),
                name="proposal_option_duration_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(service_total_amount__gte=0),
                name="proposal_option_service_total_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(penalties_total_amount__gte=0),
                name="proposal_option_penalties_total_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(total_amount__gte=0),
                name="proposal_option_total_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["proposal", "sort_order"]),
            models.Index(fields=["start_at", "end_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.proposal_id} option {self.sort_order}"


class AppointmentProposalOptionItem(models.Model):
    """Snapshotted proposed service line for a proposal option."""

    option = models.ForeignKey(
        "appointments.AppointmentProposalOption",
        on_delete=models.CASCADE,
        related_name="service_items",
        verbose_name=_("option"),
    )
    service = models.ForeignKey(
        "catalog.Service",
        on_delete=models.SET_NULL,
        related_name="appointment_proposal_items",
        null=True,
        blank=True,
        verbose_name=_("service"),
    )
    category_name_snapshot = models.CharField(
        _("category name snapshot"), max_length=255, blank=True
    )
    service_name_snapshot = models.CharField(_("service name snapshot"), max_length=255)
    description_snapshot = models.TextField(_("description snapshot"), blank=True)
    price_amount = models.PositiveIntegerField(_("price amount"))
    currency = models.CharField(_("currency"), max_length=3, default=DEFAULT_CURRENCY)
    duration_minutes = models.PositiveIntegerField(_("duration in minutes"))
    is_custom = models.BooleanField(_("custom service"), default=False)
    is_overridden = models.BooleanField(_("overridden"), default=False)
    sort_order = models.PositiveIntegerField(_("sort order"), default=0)

    class Meta:
        verbose_name = _("proposal option service item")
        verbose_name_plural = _("proposal option service items")
        ordering = ["sort_order", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(price_amount__gte=0),
                name="proposal_item_price_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(duration_minutes__gt=0),
                name="proposal_item_duration_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["option", "sort_order"]),
            models.Index(fields=["service"]),
        ]

    def __str__(self) -> str:
        return self.service_name_snapshot

    def clean(self) -> None:
        super().clean()
        if (
            self.service_id
            and self.option_id
            and self.service.partner_id != self.option.proposal.partner_id
        ):
            raise ValidationError(
                {"service": "Proposal item service must belong to the same partner."}
            )


FINAL_APPOINTMENT_STATUSES = (
    Appointment.Status.CANCELLED_BY_CLIENT,
    Appointment.Status.CANCELLED_BY_MASTER,
    Appointment.Status.COMPLETED,
    Appointment.Status.NO_SHOW,
)

BLOCKING_APPOINTMENT_STATUSES = (Appointment.Status.CONFIRMED,)
