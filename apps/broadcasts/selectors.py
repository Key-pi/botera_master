from __future__ import annotations

from datetime import timedelta

from django.db.models import Exists, OuterRef, QuerySet
from django.utils import timezone

from apps.appointments.models import Appointment, AppointmentServiceItem
from apps.broadcasts.models import BroadcastCampaign, BroadcastRecipient
from apps.clients.models import ClientProfile
from apps.partners.models import Partner

ACTIVE_FUTURE_APPOINTMENT_STATUSES = (
    Appointment.Status.REQUESTED,
    Appointment.Status.CONFIRMED,
)


def campaigns_for_partner(partner: Partner) -> QuerySet[BroadcastCampaign]:
    """Return broadcast campaigns owned by one partner."""

    return BroadcastCampaign.objects.filter(partner=partner).select_related(
        "partner",
        "created_by",
    )


def recipients_for_campaign(campaign: BroadcastCampaign) -> QuerySet[BroadcastRecipient]:
    """Return per-client broadcast delivery rows for one campaign."""

    return BroadcastRecipient.objects.filter(campaign=campaign).select_related(
        "partner",
        "campaign",
        "client_profile",
        "client_profile__telegram_identity",
    )


def due_broadcast_recipients(*, now=None) -> QuerySet[BroadcastRecipient]:
    """Return queued recipients ready for the broadcast worker."""

    current_time = now or timezone.now()
    return (
        BroadcastRecipient.objects.filter(
            status=BroadcastRecipient.Status.QUEUED,
        )
        .filter(next_attempt_at__lte=current_time)
        .select_related(
            "partner",
            "campaign",
            "client_profile",
            "client_profile__telegram_identity",
        )
        .order_by("next_attempt_at", "created_at")
    )


def eligible_broadcast_clients(
    *,
    partner: Partner,
    filters: dict | None = None,
    now=None,
) -> QuerySet[ClientProfile]:
    """Return clients eligible for one partner marketing broadcast."""

    current_time = now or timezone.now()
    cleaned_filters = filters or {}
    queryset = ClientProfile.objects.filter(
        partner=partner,
        marketing_opt_out=False,
        bot_unreachable=False,
        telegram_identity__isnull=False,
    ).select_related("telegram_identity", "partner")

    service_id = cleaned_filters.get("service_id")
    if service_id:
        service_match = AppointmentServiceItem.objects.filter(
            appointment__client_profile=OuterRef("pk"),
            appointment__partner=partner,
            appointment__status=Appointment.Status.COMPLETED,
            service_id=service_id,
        )
        queryset = queryset.annotate(had_service=Exists(service_match)).filter(had_service=True)

    long_absent_days = cleaned_filters.get("long_absent_days")
    if long_absent_days:
        cutoff = current_time - timedelta(days=int(long_absent_days))
        recent_completed = Appointment.objects.filter(
            partner=partner,
            client_profile=OuterRef("pk"),
            status=Appointment.Status.COMPLETED,
            start_at__gte=cutoff,
        )
        queryset = queryset.annotate(has_recent_visit=Exists(recent_completed)).filter(
            has_recent_visit=False
        )

    future_appointments = Appointment.objects.filter(
        partner=partner,
        client_profile=OuterRef("pk"),
        status__in=ACTIVE_FUTURE_APPOINTMENT_STATUSES,
        start_at__gt=current_time,
    )
    if cleaned_filters.get("has_future_appointment") is True:
        queryset = queryset.annotate(has_future=Exists(future_appointments)).filter(
            has_future=True
        )
    if cleaned_filters.get("without_future_appointment") is True:
        queryset = queryset.annotate(has_future=Exists(future_appointments)).filter(
            has_future=False
        )

    return queryset.order_by("name", "created_at")
