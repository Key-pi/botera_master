from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone

from apps.accounts.services import ensure_partner_access
from apps.appointments.models import (
    FINAL_APPOINTMENT_STATUSES,
    Appointment,
    AppointmentServiceItem,
)
from apps.appointments.selectors import confirmed_appointments_overlapping
from apps.catalog.models import DEFAULT_CURRENCY, Service
from apps.schedule.models import BookingResource, PartnerScheduleSettings
from core.database.locks import (
    BOOKING_RESOURCE_ADVISORY_LOCK_NAMESPACE,
    acquire_postgres_advisory_xact_lock,
)

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.clients.models import ClientProfile
    from apps.partners.models import Partner


@dataclass(frozen=True)
class AppointmentServiceItemInput:
    """Input for one appointment service snapshot line."""

    service: Service | None = None
    service_name: str = ""
    description: str = ""
    price_amount: int | None = None
    currency: str = ""
    duration_minutes: int | None = None
    sort_order: int = 0


ALLOWED_STATUS_TRANSITIONS = {
    Appointment.Status.REQUESTED: {
        Appointment.Status.CONFIRMED,
        Appointment.Status.CANCELLED_BY_CLIENT,
        Appointment.Status.CANCELLED_BY_MASTER,
    },
    Appointment.Status.CONFIRMED: {
        Appointment.Status.COMPLETED,
        Appointment.Status.NO_SHOW,
        Appointment.Status.CANCELLED_BY_CLIENT,
        Appointment.Status.CANCELLED_BY_MASTER,
    },
}


def create_requested_appointment(
    *,
    actor: User | None,
    partner: Partner,
    booking_resource: BookingResource,
    client_profile: ClientProfile,
    start_at: datetime,
    service_items: Sequence[AppointmentServiceItemInput],
    client_comment: str = "",
    is_near_fit: bool = False,
    source: str = Appointment.Source.CLIENT_BOT,
    confirmation_mode_snapshot: str = "",
) -> Appointment:
    """Create a requested appointment without blocking the public slot."""

    _ensure_actor_or_runtime_access(actor=actor, partner=partner)
    appointment_data = _prepare_appointment_data(
        partner=partner,
        booking_resource=booking_resource,
        client_profile=client_profile,
        start_at=start_at,
        service_items=service_items,
        status=Appointment.Status.REQUESTED,
        source=source,
        confirmation_mode_snapshot=confirmation_mode_snapshot,
        client_comment=client_comment,
        is_near_fit=is_near_fit,
    )
    snapshot_items = appointment_data.pop("_snapshot_items")
    now = timezone.now()
    with transaction.atomic():
        appointment = Appointment.objects.create(
            **appointment_data,
            created_by=actor,
            requested_at=now,
            status_changed_at=now,
        )
        _create_snapshot_items(
            appointment=appointment,
            snapshot_items=snapshot_items,
        )
        _on_commit_schedule_requested_notifications(appointment.id)
    return appointment


def create_confirmed_appointment(
    *,
    actor: User | None,
    partner: Partner,
    booking_resource: BookingResource,
    client_profile: ClientProfile,
    start_at: datetime,
    service_items: Sequence[AppointmentServiceItemInput],
    client_comment: str = "",
    private_master_note: str = "",
    source: str = Appointment.Source.CLIENT_BOT,
    confirmation_mode_snapshot: str = "",
) -> Appointment:
    """Create a confirmed appointment with resource-scoped overlap protection."""

    _ensure_actor_or_runtime_access(actor=actor, partner=partner)
    appointment_data = _prepare_appointment_data(
        partner=partner,
        booking_resource=booking_resource,
        client_profile=client_profile,
        start_at=start_at,
        service_items=service_items,
        status=Appointment.Status.CONFIRMED,
        source=source,
        confirmation_mode_snapshot=confirmation_mode_snapshot,
        client_comment=client_comment,
        private_master_note=private_master_note,
        is_near_fit=False,
    )
    snapshot_items = appointment_data.pop("_snapshot_items")
    now = timezone.now()
    with transaction.atomic():
        acquire_booking_resource_advisory_lock_if_supported(booking_resource)
        _raise_if_confirmed_overlap(
            booking_resource=booking_resource,
            start_at=appointment_data["start_at"],
            end_at=appointment_data["end_at"],
        )
        appointment = Appointment.objects.create(
            **appointment_data,
            created_by=actor,
            confirmed_at=now,
            status_changed_at=now,
        )
        _create_snapshot_items(
            appointment=appointment,
            snapshot_items=snapshot_items,
        )
        _on_commit_schedule_confirmed_notifications(appointment.id)
    return appointment


def create_manual_appointment(
    *,
    actor: User,
    partner: Partner,
    booking_resource: BookingResource,
    client_profile: ClientProfile,
    start_at: datetime,
    service_items: Sequence[AppointmentServiceItemInput],
    client_comment: str = "",
    private_master_note: str = "",
) -> Appointment:
    """Create a partner-admin manual appointment, confirmed by default."""

    return create_confirmed_appointment(
        actor=actor,
        partner=partner,
        booking_resource=booking_resource,
        client_profile=client_profile,
        start_at=start_at,
        service_items=service_items,
        client_comment=client_comment,
        private_master_note=private_master_note,
        source=Appointment.Source.PARTNER_ADMIN,
        confirmation_mode_snapshot=PartnerScheduleSettings.ConfirmationMode.MANUAL,
    )


def confirm_requested_appointment(*, actor: User, appointment: Appointment) -> Appointment:
    """Confirm a requested appointment after a fresh overlap check."""

    ensure_partner_access(actor, appointment.partner)
    now = timezone.now()
    with transaction.atomic():
        appointment = Appointment.objects.select_for_update().get(id=appointment.id)
        _validate_status_transition(
            current_status=appointment.status,
            new_status=Appointment.Status.CONFIRMED,
        )
        acquire_booking_resource_advisory_lock_if_supported(appointment.booking_resource)
        _raise_if_confirmed_overlap(
            booking_resource=appointment.booking_resource,
            start_at=appointment.start_at,
            end_at=appointment.end_at,
            exclude_appointment=appointment,
        )
        appointment.status = Appointment.Status.CONFIRMED
        appointment.confirmed_at = now
        appointment.status_changed_at = now
        appointment.save(
            update_fields=["status", "confirmed_at", "status_changed_at", "updated_at"]
        )
        _on_commit_refresh_confirmed_notifications(appointment.id)
    return appointment


def transition_appointment_status(
    *,
    actor: User,
    appointment: Appointment,
    new_status: str,
    reason: str = "",
) -> Appointment:
    """Move an appointment through approved MVP status transitions."""

    ensure_partner_access(actor, appointment.partner)
    if new_status == Appointment.Status.CONFIRMED:
        return confirm_requested_appointment(actor=actor, appointment=appointment)

    now = timezone.now()
    with transaction.atomic():
        appointment = Appointment.objects.select_for_update().get(id=appointment.id)
        _validate_status_transition(current_status=appointment.status, new_status=new_status)
        appointment.status = new_status
        appointment.client_visible_reason = reason
        _apply_status_timestamp(appointment=appointment, status=new_status, changed_at=now)
        appointment.status_changed_at = now
        appointment.save(
            update_fields=[
                "status",
                "client_visible_reason",
                "cancelled_at",
                "completed_at",
                "no_show_at",
                "status_changed_at",
                "updated_at",
            ]
        )
        _release_active_penalties_if_cancelled(appointment)
        _on_commit_stale_appointment_notifications(appointment.id)
    return appointment


def cancel_requested_appointment_by_client(
    *,
    client_profile: ClientProfile,
    appointment: Appointment,
    reason: str = "",
) -> Appointment:
    """Cancel a pending request from the client bot context."""

    if appointment.client_profile_id != client_profile.id:
        raise ValidationError({"appointment": "Appointment does not belong to this client."})

    now = timezone.now()
    with transaction.atomic():
        appointment = Appointment.objects.select_for_update().get(id=appointment.id)
        if appointment.client_profile_id != client_profile.id:
            raise ValidationError({"appointment": "Appointment does not belong to this client."})
        if appointment.status != Appointment.Status.REQUESTED:
            raise ValidationError({"status": "Only requested appointments can be cancelled here."})

        appointment.status = Appointment.Status.CANCELLED_BY_CLIENT
        appointment.client_visible_reason = reason
        appointment.cancelled_at = now
        appointment.status_changed_at = now
        appointment.save(
            update_fields=[
                "status",
                "client_visible_reason",
                "cancelled_at",
                "status_changed_at",
                "updated_at",
            ]
        )
        _release_active_penalties_if_cancelled(appointment)
        _on_commit_stale_appointment_notifications(appointment.id)
    return appointment


def correct_final_appointment_status(
    *,
    actor: User,
    appointment: Appointment,
    new_status: str,
    reason: str,
) -> Appointment:
    """Correct a final appointment outcome with a required audit reason."""

    ensure_partner_access(actor, appointment.partner)
    cleaned_reason = reason.strip()
    if not cleaned_reason:
        raise ValidationError({"reason": "Correction reason is required."})
    if new_status not in (*FINAL_APPOINTMENT_STATUSES, Appointment.Status.CONFIRMED):
        raise ValidationError({"status": "Unsupported correction target status."})

    now = timezone.now()
    with transaction.atomic():
        appointment = Appointment.objects.select_for_update().get(id=appointment.id)
        if appointment.status not in FINAL_APPOINTMENT_STATUSES:
            raise ValidationError({"status": "Only final appointments can be corrected here."})
        if new_status == Appointment.Status.CONFIRMED:
            acquire_booking_resource_advisory_lock_if_supported(appointment.booking_resource)
            _raise_if_confirmed_overlap(
                booking_resource=appointment.booking_resource,
                start_at=appointment.start_at,
                end_at=appointment.end_at,
                exclude_appointment=appointment,
            )
        appointment.status = new_status
        appointment.last_correction_reason = cleaned_reason
        appointment.last_correction_at = now
        appointment.last_correction_by = actor
        _apply_status_timestamp(appointment=appointment, status=new_status, changed_at=now)
        appointment.status_changed_at = now
        appointment.save(
            update_fields=[
                "status",
                "cancelled_at",
                "completed_at",
                "no_show_at",
                "confirmed_at",
                "status_changed_at",
                "last_correction_reason",
                "last_correction_at",
                "last_correction_by",
                "updated_at",
            ]
        )
        if new_status == Appointment.Status.CONFIRMED:
            _on_commit_refresh_confirmed_notifications(appointment.id)
        else:
            _release_active_penalties_if_cancelled(appointment)
            _on_commit_stale_appointment_notifications(appointment.id)
    return appointment


def acquire_booking_resource_advisory_lock_if_supported(
    booking_resource: BookingResource,
) -> None:
    """Acquire the real PostgreSQL lock in production; no-op for local SQLite tests."""

    if connection.vendor != "postgresql":
        return
    acquire_postgres_advisory_xact_lock(
        namespace=BOOKING_RESOURCE_ADVISORY_LOCK_NAMESPACE,
        lock_key=booking_resource.advisory_lock_key,
    )


def build_service_item_snapshots(
    *,
    partner: Partner,
    service_items: Sequence[AppointmentServiceItemInput],
) -> tuple[dict, ...]:
    """Build validated service item snapshots without creating an appointment."""

    return _build_snapshot_items(partner=partner, inputs=service_items)


def replace_appointment_service_items_from_snapshots(
    *,
    appointment: Appointment,
    snapshot_items: Sequence[dict],
) -> None:
    """Replace appointment item snapshots after an accepted client-visible change."""

    AppointmentServiceItem.objects.filter(appointment=appointment).delete()
    _create_snapshot_items(appointment=appointment, snapshot_items=snapshot_items)


def refresh_confirmed_notifications_after_commit(appointment_id) -> None:
    """Refresh pending notifications once a confirmed appointment changed safely."""

    _on_commit_refresh_confirmed_notifications(appointment_id)


def stale_appointment_notifications_after_commit(appointment_id) -> None:
    """Stale pending appointment notifications after cancellation/final changes."""

    _on_commit_stale_appointment_notifications(appointment_id)


def _on_commit_schedule_requested_notifications(appointment_id) -> None:
    transaction.on_commit(lambda: _schedule_requested_notifications(appointment_id))


def _on_commit_schedule_confirmed_notifications(appointment_id) -> None:
    transaction.on_commit(lambda: _schedule_confirmed_notifications(appointment_id))


def _on_commit_refresh_confirmed_notifications(appointment_id) -> None:
    transaction.on_commit(lambda: _refresh_confirmed_notifications(appointment_id))


def _on_commit_stale_appointment_notifications(appointment_id) -> None:
    transaction.on_commit(lambda: _stale_appointment_notifications(appointment_id))


def _schedule_requested_notifications(appointment_id) -> None:
    from apps.notifications.services import schedule_stale_requested_notification

    appointment = Appointment.objects.select_related("partner", "client_profile").get(
        id=appointment_id
    )
    schedule_stale_requested_notification(appointment=appointment)


def _schedule_confirmed_notifications(appointment_id) -> None:
    from apps.notifications.services import schedule_reminders_for_confirmed_appointment

    appointment = Appointment.objects.select_related("partner", "client_profile").get(
        id=appointment_id
    )
    schedule_reminders_for_confirmed_appointment(appointment=appointment)


def _refresh_confirmed_notifications(appointment_id) -> None:
    from apps.notifications.services import (
        cancel_pending_notifications_for_appointment,
        schedule_reminders_for_confirmed_appointment,
    )

    appointment = Appointment.objects.select_related("partner", "client_profile").get(
        id=appointment_id
    )
    cancel_pending_notifications_for_appointment(appointment=appointment)
    schedule_reminders_for_confirmed_appointment(appointment=appointment)


def _stale_appointment_notifications(appointment_id) -> None:
    from apps.notifications.services import cancel_pending_notifications_for_appointment

    appointment = Appointment.objects.get(id=appointment_id)
    cancel_pending_notifications_for_appointment(appointment=appointment)


def _prepare_appointment_data(
    *,
    partner: Partner,
    booking_resource: BookingResource,
    client_profile: ClientProfile,
    start_at: datetime,
    service_items: Sequence[AppointmentServiceItemInput],
    status: str,
    source: str,
    confirmation_mode_snapshot: str,
    client_comment: str,
    is_near_fit: bool,
    private_master_note: str = "",
) -> dict:
    _validate_partner_scope(
        partner=partner,
        booking_resource=booking_resource,
        client_profile=client_profile,
    )
    _validate_aware_datetime(start_at, "start_at")
    snapshot_items = _build_snapshot_items(partner=partner, inputs=service_items)
    duration_minutes = sum(item["duration_minutes"] for item in snapshot_items)
    end_at = start_at + timedelta(minutes=duration_minutes)
    service_total_amount = sum(item["price_amount"] for item in snapshot_items)
    currency = _single_currency(snapshot_items)
    confirmation_snapshot = confirmation_mode_snapshot or _confirmation_mode_snapshot(partner)

    return {
        "partner": partner,
        "booking_resource": booking_resource,
        "client_profile": client_profile,
        "start_at": start_at,
        "end_at": end_at,
        "status": status,
        "source": source,
        "confirmation_mode_snapshot": confirmation_snapshot,
        "is_near_fit": is_near_fit,
        "client_comment": client_comment,
        "private_master_note": private_master_note,
        "service_total_amount": service_total_amount,
        "penalties_total_amount": 0,
        "total_amount": service_total_amount,
        "currency": currency,
        "_snapshot_items": snapshot_items,
    }


def _build_snapshot_items(
    *,
    partner: Partner,
    inputs: Sequence[AppointmentServiceItemInput],
) -> tuple[dict, ...]:
    if not inputs:
        raise ValidationError({"service_items": "At least one service item is required."})

    snapshot_items: list[dict] = []
    for index, item in enumerate(inputs):
        if item.service is not None:
            snapshot_items.append(
                _snapshot_catalog_service(partner=partner, item=item, index=index)
            )
        else:
            snapshot_items.append(_snapshot_custom_service(item=item, index=index))
    return tuple(snapshot_items)


def _snapshot_catalog_service(
    *,
    partner: Partner,
    item: AppointmentServiceItemInput,
    index: int,
) -> dict:
    service = item.service
    if service is None:
        raise ValidationError({"service": "Service is required."})
    if service.partner_id != partner.id:
        raise ValidationError({"service": "Service must belong to this partner."})

    price_amount = service.price_amount if item.price_amount is None else item.price_amount
    duration_minutes = (
        service.duration_minutes if item.duration_minutes is None else item.duration_minutes
    )
    _validate_money_and_duration(price_amount=price_amount, duration_minutes=duration_minutes)
    currency = _normalize_currency(item.currency or service.currency)
    if currency != service.currency:
        raise ValidationError(
            {"currency": "Currency override is not supported for catalog services."}
        )

    return {
        "service": service,
        "category_name_snapshot": service.category.name,
        "service_name_snapshot": item.service_name.strip() or service.name,
        "description_snapshot": item.description if item.description else service.description,
        "price_amount": price_amount,
        "currency": currency,
        "duration_minutes": duration_minutes,
        "is_custom": False,
        "is_overridden": price_amount != service.price_amount
        or duration_minutes != service.duration_minutes,
        "sort_order": item.sort_order or index,
    }


def _snapshot_custom_service(*, item: AppointmentServiceItemInput, index: int) -> dict:
    service_name = item.service_name.strip()
    if not service_name:
        raise ValidationError({"service_name": "Custom service name is required."})
    if item.price_amount is None:
        raise ValidationError({"price_amount": "Custom service price is required."})
    if item.duration_minutes is None:
        raise ValidationError({"duration_minutes": "Custom service duration is required."})
    _validate_money_and_duration(
        price_amount=item.price_amount,
        duration_minutes=item.duration_minutes,
    )

    return {
        "service": None,
        "category_name_snapshot": "",
        "service_name_snapshot": service_name,
        "description_snapshot": item.description,
        "price_amount": item.price_amount,
        "currency": _normalize_currency(item.currency or DEFAULT_CURRENCY),
        "duration_minutes": item.duration_minutes,
        "is_custom": True,
        "is_overridden": False,
        "sort_order": item.sort_order or index,
    }


def _create_snapshot_items(*, appointment: Appointment, snapshot_items: Sequence[dict]) -> None:
    AppointmentServiceItem.objects.bulk_create(
        [AppointmentServiceItem(appointment=appointment, **item) for item in snapshot_items]
    )


def _raise_if_confirmed_overlap(
    *,
    booking_resource: BookingResource,
    start_at: datetime,
    end_at: datetime,
    exclude_appointment: Appointment | None = None,
) -> None:
    if confirmed_appointments_overlapping(
        booking_resource=booking_resource,
        start_at=start_at,
        end_at=end_at,
        exclude_appointment=exclude_appointment,
    ).exists():
        raise ValidationError({"start_at": "This time is already occupied."})


def _validate_status_transition(*, current_status: str, new_status: str) -> None:
    if new_status not in Appointment.Status.values:
        raise ValidationError({"status": "Unknown appointment status."})
    if new_status not in ALLOWED_STATUS_TRANSITIONS.get(current_status, set()):
        raise ValidationError({"status": f"Cannot change {current_status} to {new_status}."})


def _apply_status_timestamp(
    *,
    appointment: Appointment,
    status: str,
    changed_at: datetime,
) -> None:
    if status == Appointment.Status.CONFIRMED:
        appointment.confirmed_at = changed_at
    elif status in {Appointment.Status.CANCELLED_BY_CLIENT, Appointment.Status.CANCELLED_BY_MASTER}:
        appointment.cancelled_at = changed_at
    elif status == Appointment.Status.COMPLETED:
        appointment.completed_at = changed_at
    elif status == Appointment.Status.NO_SHOW:
        appointment.no_show_at = changed_at


def _release_active_penalties_if_cancelled(appointment: Appointment) -> None:
    if appointment.status not in {
        Appointment.Status.CANCELLED_BY_CLIENT,
        Appointment.Status.CANCELLED_BY_MASTER,
    }:
        return

    from apps.finance.services import release_active_penalty_applications_for_appointment

    release_active_penalty_applications_for_appointment(appointment=appointment)


def _confirmation_mode_snapshot(partner: Partner) -> str:
    settings = PartnerScheduleSettings.objects.filter(partner=partner).first()
    if settings and settings.confirmation_mode:
        return settings.confirmation_mode
    return ""


def _ensure_actor_or_runtime_access(*, actor: User | None, partner: Partner) -> None:
    if actor is not None:
        ensure_partner_access(actor, partner)


def _validate_partner_scope(
    *,
    partner: Partner,
    booking_resource: BookingResource,
    client_profile: ClientProfile,
) -> None:
    if booking_resource.partner_id != partner.id:
        raise ValidationError({"booking_resource": "Booking resource must belong to this partner."})
    if client_profile.partner_id != partner.id:
        raise ValidationError({"client_profile": "Client profile must belong to this partner."})


def _validate_aware_datetime(value: datetime, field_name: str) -> None:
    if timezone.is_naive(value):
        raise ValidationError({field_name: "Datetime must be timezone-aware."})


def _validate_money_and_duration(*, price_amount: int, duration_minutes: int) -> None:
    if price_amount < 0:
        raise ValidationError({"price_amount": "Price cannot be negative."})
    if duration_minutes <= 0:
        raise ValidationError({"duration_minutes": "Duration must be positive."})


def _normalize_currency(currency: str) -> str:
    normalized_currency = currency.strip().upper()
    if len(normalized_currency) != 3:
        raise ValidationError({"currency": "Currency must be a three-letter code."})
    return normalized_currency


def _single_currency(snapshot_items: Sequence[dict]) -> str:
    currencies = {item["currency"] for item in snapshot_items}
    if len(currencies) != 1:
        raise ValidationError({"currency": "All service items must use one currency."})
    return currencies.pop()
