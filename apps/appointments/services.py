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
from apps.audit.services import record_audit_event
from apps.catalog.models import DEFAULT_CURRENCY, Service
from apps.schedule.models import BookingResource, PartnerScheduleSettings
from core.database.locks import (
    BOOKING_RESOURCE_ADVISORY_LOCK_NAMESPACE,
    acquire_postgres_advisory_xact_lock,
)

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.clients.models import ClientProfile
    from apps.finance.models import Penalty
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


@dataclass(frozen=True, slots=True)
class ClientCancellationPolicy:
    """Current client cancellation rules for one appointment."""

    allowed: bool
    reason_code: str = ""
    requires_penalty_confirmation: bool = False
    penalty_amount: int = 0
    currency: str = DEFAULT_CURRENCY
    deadline_hours: int = 0


@dataclass(frozen=True, slots=True)
class ClientCancellationResult:
    """Persisted outcome of one client cancellation."""

    appointment: Appointment
    penalty: Penalty | None = None


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


def available_appointment_status_targets(
    *,
    appointment: Appointment,
    now: datetime | None = None,
) -> tuple[str, ...]:
    """Return status actions that make sense for the current appointment state."""

    current_time = now or timezone.now()
    if appointment.status == Appointment.Status.REQUESTED:
        return (
            Appointment.Status.CONFIRMED,
            Appointment.Status.CANCELLED_BY_MASTER,
        )
    if appointment.status == Appointment.Status.CONFIRMED:
        targets: list[str] = [Appointment.Status.CANCELLED_BY_MASTER]
        if appointment.start_at <= current_time:
            targets[0:0] = [
                Appointment.Status.COMPLETED,
                Appointment.Status.NO_SHOW,
            ]
        return tuple(targets)
    if appointment.status in FINAL_APPOINTMENT_STATUSES:
        targets = [status for status in FINAL_APPOINTMENT_STATUSES if status != appointment.status]
        if appointment.start_at > current_time:
            targets.append(Appointment.Status.CONFIRMED)
        return tuple(targets)
    return ()


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
        if actor is None and source == Appointment.Source.CLIENT_BOT:
            _on_commit_schedule_master_client_booked_notification(appointment.id)
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

    from apps.notifications.services import schedule_manual_appointment_created_notification

    with transaction.atomic():
        appointment = create_confirmed_appointment(
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
        schedule_manual_appointment_created_notification(appointment=appointment)
        return appointment


def create_manual_appointment_with_client(
    *,
    actor: User,
    partner: Partner,
    booking_resource: BookingResource,
    start_at: datetime,
    service_items: Sequence[AppointmentServiceItemInput],
    client_profile: ClientProfile | None = None,
    new_client_name: str = "",
    new_client_phone: str = "",
    client_comment: str = "",
    private_master_note: str = "",
) -> Appointment:
    """Create a manual client when needed and its confirmed appointment atomically.

    Keeping both writes in one use case avoids leaving an orphan manual client
    when the confirmed-overlap check rejects the appointment.
    """

    ensure_partner_access(actor, partner)
    cleaned_name = new_client_name.strip()
    cleaned_phone = new_client_phone.strip()
    has_new_client_data = bool(cleaned_name or cleaned_phone)
    if client_profile is not None and has_new_client_data:
        raise ValidationError(
            {"client_profile": "Choose an existing client or create a new one, not both."}
        )
    if client_profile is None and not has_new_client_data:
        raise ValidationError(
            {"client": "Choose an existing client or enter a new client name or phone."}
        )

    with transaction.atomic():
        resolved_client = client_profile
        if resolved_client is None:
            from apps.clients.services import create_manual_client_profile

            resolved_client = create_manual_client_profile(
                actor=actor,
                partner=partner,
                name=cleaned_name,
                phone=cleaned_phone,
            )
        return create_manual_appointment(
            actor=actor,
            partner=partner,
            booking_resource=booking_resource,
            client_profile=resolved_client,
            start_at=start_at,
            service_items=service_items,
            client_comment=client_comment,
            private_master_note=private_master_note,
        )


def confirm_requested_appointment(*, actor: User, appointment: Appointment) -> Appointment:
    """Confirm a requested appointment after a fresh overlap check."""

    ensure_partner_access(actor, appointment.partner)
    now = timezone.now()
    with transaction.atomic():
        appointment = Appointment.objects.select_for_update().get(id=appointment.id)
        old_status = appointment.status
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
        _record_status_audit(
            actor=actor,
            appointment=appointment,
            old_status=old_status,
            new_status=Appointment.Status.CONFIRMED,
        )
        _on_commit_refresh_confirmed_notifications(appointment.id)
        _on_commit_schedule_client_request_confirmed_notification(appointment.id)
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

    cleaned_reason = reason.strip()
    now = timezone.now()
    with transaction.atomic():
        appointment = Appointment.objects.select_for_update().get(id=appointment.id)
        old_status = appointment.status
        _validate_status_transition(current_status=appointment.status, new_status=new_status)
        _validate_status_timing(
            appointment=appointment,
            new_status=new_status,
            changed_at=now,
        )
        appointment.status = new_status
        appointment.client_visible_reason = cleaned_reason
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
        _record_status_audit(
            actor=actor,
            appointment=appointment,
            old_status=old_status,
            new_status=new_status,
            reason=cleaned_reason,
        )
        _release_active_penalties_if_cancelled(appointment)
        _on_commit_stale_appointment_notifications(appointment.id)
        if (
            old_status == Appointment.Status.REQUESTED
            and new_status == Appointment.Status.CANCELLED_BY_MASTER
        ):
            _on_commit_schedule_client_request_declined_notification(appointment.id)
        elif new_status in {
            Appointment.Status.CANCELLED_BY_MASTER,
            Appointment.Status.COMPLETED,
            Appointment.Status.NO_SHOW,
        }:
            _on_commit_schedule_client_status_notification(appointment.id)
    return appointment


def cancel_requested_appointment_by_client(
    *,
    client_profile: ClientProfile,
    appointment: Appointment,
    reason: str = "",
) -> Appointment:
    """Cancel a pending request from the client bot context."""

    result = _cancel_appointment_by_client(
        client_profile=client_profile,
        appointment=appointment,
        reason=reason,
        expected_status=Appointment.Status.REQUESTED,
    )
    return result.appointment


def client_cancellation_policy(
    *,
    client_profile: ClientProfile,
    appointment: Appointment,
    now: datetime | None = None,
) -> ClientCancellationPolicy:
    """Return the current partner rules for a client-owned appointment."""

    if appointment.client_profile_id != client_profile.id:
        raise ValidationError({"appointment": "Appointment does not belong to this client."})

    return _client_cancellation_policy(
        appointment=appointment,
        cancelled_at=now or timezone.now(),
    )


def cancel_appointment_by_client(
    *,
    client_profile: ClientProfile,
    appointment: Appointment,
    accept_late_penalty: bool = False,
    reason: str = "",
    now: datetime | None = None,
) -> ClientCancellationResult:
    """Cancel a requested/confirmed booking after a fresh policy check.

    The appointment row is locked before policy evaluation so a stale Telegram
    confirmation cannot bypass a newly reached deadline or create two penalties.
    """

    return _cancel_appointment_by_client(
        client_profile=client_profile,
        appointment=appointment,
        accept_late_penalty=accept_late_penalty,
        reason=reason,
        now=now,
    )


def _cancel_appointment_by_client(
    *,
    client_profile: ClientProfile,
    appointment: Appointment,
    accept_late_penalty: bool = False,
    reason: str = "",
    now: datetime | None = None,
    expected_status: str | None = None,
) -> ClientCancellationResult:
    """Persist a client cancellation while preserving wrapper-specific rules."""

    if appointment.client_profile_id != client_profile.id:
        raise ValidationError({"appointment": "Appointment does not belong to this client."})

    cancelled_at = now or timezone.now()
    with transaction.atomic():
        appointment = (
            Appointment.objects.select_for_update()
            .select_related("partner", "client_profile")
            .get(id=appointment.id)
        )
        if appointment.client_profile_id != client_profile.id:
            raise ValidationError({"appointment": "Appointment does not belong to this client."})
        if expected_status is not None and appointment.status != expected_status:
            raise ValidationError(
                {"status": f"Only {expected_status} appointments can be cancelled here."}
            )

        policy = _client_cancellation_policy(
            appointment=appointment,
            cancelled_at=cancelled_at,
        )
        if not policy.allowed:
            raise ValidationError(
                {"appointment": _client_cancellation_error_message(policy.reason_code)}
            )
        if policy.requires_penalty_confirmation and not accept_late_penalty:
            raise ValidationError(
                {"penalty_confirmation": "Confirm the late-cancellation penalty first."}
            )

        old_status = appointment.status
        appointment.status = Appointment.Status.CANCELLED_BY_CLIENT
        appointment.client_visible_reason = reason.strip()
        appointment.cancelled_at = cancelled_at
        appointment.status_changed_at = cancelled_at
        appointment.save(
            update_fields=[
                "status",
                "client_visible_reason",
                "cancelled_at",
                "status_changed_at",
                "updated_at",
            ]
        )
        _record_status_audit(
            actor=None,
            appointment=appointment,
            old_status=old_status,
            new_status=Appointment.Status.CANCELLED_BY_CLIENT,
            reason=appointment.client_visible_reason,
        )
        _release_active_penalties_if_cancelled(appointment)
        penalty = None
        if policy.requires_penalty_confirmation:
            from apps.finance.services import create_late_cancel_penalty_for_appointment

            penalty = create_late_cancel_penalty_for_appointment(
                actor=None,
                appointment=appointment,
            )
        _on_commit_stale_appointment_notifications(appointment.id)
        _on_commit_schedule_master_client_cancelled_notification(appointment.id)
    return ClientCancellationResult(appointment=appointment, penalty=penalty)


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
        if new_status == appointment.status:
            raise ValidationError({"status": "Choose a different status for the correction."})
        if new_status == Appointment.Status.CONFIRMED:
            if appointment.start_at <= now:
                raise ValidationError(
                    {"status": "Only a future appointment can be restored to confirmed."}
                )
            acquire_booking_resource_advisory_lock_if_supported(appointment.booking_resource)
            _raise_if_confirmed_overlap(
                booking_resource=appointment.booking_resource,
                start_at=appointment.start_at,
                end_at=appointment.end_at,
                exclude_appointment=appointment,
            )
        old_status = appointment.status
        appointment.status = new_status
        appointment.client_visible_reason = ""
        appointment.last_correction_reason = cleaned_reason
        appointment.last_correction_at = now
        appointment.last_correction_by = actor
        _clear_final_status_timestamps(appointment)
        _apply_status_timestamp(appointment=appointment, status=new_status, changed_at=now)
        appointment.status_changed_at = now
        appointment.save(
            update_fields=[
                "status",
                "client_visible_reason",
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
        _record_status_audit(
            actor=actor,
            appointment=appointment,
            old_status=old_status,
            new_status=new_status,
            reason=cleaned_reason,
            correction=True,
        )
        if new_status == Appointment.Status.CONFIRMED:
            _on_commit_refresh_confirmed_notifications(appointment.id)
        else:
            _release_active_penalties_if_cancelled(appointment)
            _on_commit_stale_appointment_notifications(appointment.id)
            _on_commit_schedule_client_status_notification(appointment.id)
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


def _on_commit_schedule_master_client_booked_notification(appointment_id) -> None:
    transaction.on_commit(lambda: _schedule_master_client_booked_notification(appointment_id))


def _on_commit_refresh_confirmed_notifications(appointment_id) -> None:
    transaction.on_commit(lambda: _refresh_confirmed_notifications(appointment_id))


def _on_commit_stale_appointment_notifications(appointment_id) -> None:
    transaction.on_commit(lambda: _stale_appointment_notifications(appointment_id))


def _on_commit_schedule_client_request_confirmed_notification(appointment_id) -> None:
    transaction.on_commit(lambda: _schedule_client_request_confirmed_notification(appointment_id))


def _on_commit_schedule_client_request_declined_notification(appointment_id) -> None:
    transaction.on_commit(lambda: _schedule_client_request_declined_notification(appointment_id))


def _on_commit_schedule_client_status_notification(appointment_id) -> None:
    transaction.on_commit(lambda: _schedule_client_status_notification(appointment_id))


def _on_commit_schedule_master_client_cancelled_notification(appointment_id) -> None:
    transaction.on_commit(lambda: _schedule_master_client_cancelled_notification(appointment_id))


def _schedule_requested_notifications(appointment_id) -> None:
    from apps.notifications.services import (
        schedule_master_booking_request_notification,
        schedule_stale_requested_notification,
    )

    appointment = Appointment.objects.select_related("partner", "client_profile").get(
        id=appointment_id
    )
    initial_notice = schedule_master_booking_request_notification(appointment=appointment)
    schedule_stale_requested_notification(
        appointment=appointment,
        anchor_at=initial_notice.due_at if initial_notice is not None else None,
    )


def _schedule_client_request_confirmed_notification(appointment_id) -> None:
    from apps.notifications.services import (
        schedule_client_booking_request_confirmed_notification,
    )

    appointment = Appointment.objects.select_related("partner", "client_profile").get(
        id=appointment_id
    )
    schedule_client_booking_request_confirmed_notification(appointment=appointment)


def _schedule_client_request_declined_notification(appointment_id) -> None:
    from apps.notifications.services import (
        schedule_client_booking_request_declined_notification,
    )

    appointment = Appointment.objects.select_related("partner", "client_profile").get(
        id=appointment_id
    )
    schedule_client_booking_request_declined_notification(appointment=appointment)


def _schedule_client_status_notification(appointment_id) -> None:
    from apps.notifications.services import schedule_client_appointment_status_notification

    appointment = Appointment.objects.select_related("partner", "client_profile").get(
        id=appointment_id
    )
    schedule_client_appointment_status_notification(appointment=appointment)


def _schedule_confirmed_notifications(appointment_id) -> None:
    from apps.notifications.services import schedule_reminders_for_confirmed_appointment

    appointment = Appointment.objects.select_related("partner", "client_profile").get(
        id=appointment_id
    )
    schedule_reminders_for_confirmed_appointment(appointment=appointment)


def _schedule_master_client_booked_notification(appointment_id) -> None:
    from apps.notifications.services import schedule_master_client_booked_notification

    appointment = Appointment.objects.select_related("partner", "client_profile").get(
        id=appointment_id
    )
    schedule_master_client_booked_notification(appointment=appointment)


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


def _schedule_master_client_cancelled_notification(appointment_id) -> None:
    from apps.notifications.services import schedule_master_client_cancelled_notification

    appointment = Appointment.objects.select_related("partner", "client_profile").get(
        id=appointment_id
    )
    schedule_master_client_cancelled_notification(appointment=appointment)


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


def _validate_status_timing(
    *,
    appointment: Appointment,
    new_status: str,
    changed_at: datetime,
) -> None:
    if (
        new_status in {Appointment.Status.COMPLETED, Appointment.Status.NO_SHOW}
        and appointment.start_at > changed_at
    ):
        raise ValidationError(
            {"status": "An appointment can be completed or marked no-show only after it starts."}
        )


def _client_cancellation_policy(
    *,
    appointment: Appointment,
    cancelled_at: datetime,
) -> ClientCancellationPolicy:
    if timezone.is_naive(cancelled_at):
        raise ValidationError({"now": "Datetime must be timezone-aware."})
    if appointment.status not in {
        Appointment.Status.REQUESTED,
        Appointment.Status.CONFIRMED,
    }:
        return ClientCancellationPolicy(
            allowed=False,
            reason_code="unsupported_status",
        )
    if appointment.start_at <= cancelled_at:
        return ClientCancellationPolicy(
            allowed=False,
            reason_code="appointment_started",
        )
    if appointment.status == Appointment.Status.REQUESTED:
        return ClientCancellationPolicy(allowed=True)

    from apps.finance.models import PartnerPenaltySettings
    from apps.finance.services import is_late_cancellation

    schedule_settings = PartnerScheduleSettings.objects.filter(partner=appointment.partner).first()
    deadline_hours = schedule_settings.cancellation_deadline_hours if schedule_settings else 6
    if not is_late_cancellation(appointment=appointment, cancelled_at=cancelled_at):
        return ClientCancellationPolicy(
            allowed=True,
            deadline_hours=deadline_hours,
        )

    penalty_settings = PartnerPenaltySettings.objects.filter(partner=appointment.partner).first()
    if penalty_settings is None or not penalty_settings.penalties_enabled:
        return ClientCancellationPolicy(
            allowed=False,
            reason_code="late_cancellation_blocked",
            deadline_hours=deadline_hours,
        )
    return ClientCancellationPolicy(
        allowed=True,
        requires_penalty_confirmation=True,
        penalty_amount=penalty_settings.late_cancel_penalty_amount,
        currency=penalty_settings.currency,
        deadline_hours=deadline_hours,
    )


def _client_cancellation_error_message(reason_code: str) -> str:
    messages = {
        "appointment_started": "Only future appointments can be cancelled by the client.",
        "late_cancellation_blocked": ("The cancellation deadline has passed; contact the master."),
        "unsupported_status": "This appointment can no longer be cancelled by the client.",
    }
    return messages.get(reason_code, "This appointment cannot be cancelled by the client.")


def _clear_final_status_timestamps(appointment: Appointment) -> None:
    """Clear mutually exclusive outcome timestamps before applying a correction."""

    appointment.cancelled_at = None
    appointment.completed_at = None
    appointment.no_show_at = None


def _record_status_audit(
    *,
    actor: User | None,
    appointment: Appointment,
    old_status: str,
    new_status: str,
    reason: str = "",
    correction: bool = False,
) -> None:
    event_type = "appointment.status_corrected" if correction else "appointment.status_changed"
    record_audit_event(
        partner=appointment.partner,
        actor=actor,
        event_type=event_type,
        target=appointment,
        message=f"Appointment status changed from {old_status} to {new_status}.",
        payload={"old_status": old_status, "new_status": new_status},
        reason=reason,
    )


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
