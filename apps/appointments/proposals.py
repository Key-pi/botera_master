from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.services import ensure_partner_access
from apps.appointments.models import (
    Appointment,
    AppointmentProposal,
    AppointmentProposalOption,
    AppointmentProposalOptionItem,
)
from apps.appointments.selectors import confirmed_appointments_overlapping
from apps.appointments.services import (
    AppointmentServiceItemInput,
    acquire_booking_resource_advisory_lock_if_supported,
    build_service_item_snapshots,
    refresh_confirmed_notifications_after_commit,
    replace_appointment_service_items_from_snapshots,
    stale_appointment_notifications_after_commit,
)
from apps.audit.services import record_audit_event

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.clients.models import ClientProfile


PROPOSAL_DEFAULT_LIFETIME = timedelta(hours=48)


@dataclass(frozen=True)
class AppointmentProposalOptionInput:
    """Input for one selectable appointment proposal option."""

    start_at: datetime | None = None
    service_items: Sequence[AppointmentServiceItemInput] | None = None
    penalties_total_amount: int | None = None
    is_near_fit: bool = False
    sort_order: int = 0


def create_appointment_proposal(
    *,
    actor: User | None,
    appointment: Appointment,
    proposal_type: str,
    options: Sequence[AppointmentProposalOptionInput],
    message: str = "",
    reason: str = "",
    expires_at: datetime | None = None,
) -> AppointmentProposal:
    """Create a typed appointment proposal and supersede older pending proposals."""

    if actor is not None:
        ensure_partner_access(actor, appointment.partner)
    if proposal_type not in AppointmentProposal.Type.values:
        raise ValidationError({"type": "Unknown appointment proposal type."})
    if not options:
        raise ValidationError({"options": "At least one proposal option is required."})

    expires_at = expires_at or timezone.now() + PROPOSAL_DEFAULT_LIFETIME
    _validate_aware_datetime(expires_at, "expires_at")
    now = timezone.now()

    with transaction.atomic():
        appointment = (
            Appointment.objects.select_for_update()
            .select_related("partner", "booking_resource", "client_profile")
            .get(id=appointment.id)
        )
        _validate_proposal_type_for_appointment(
            appointment=appointment,
            proposal_type=proposal_type,
        )
        AppointmentProposal.objects.filter(
            appointment=appointment,
            status=AppointmentProposal.Status.PENDING,
        ).update(status=AppointmentProposal.Status.SUPERSEDED, responded_at=now)

        proposal = AppointmentProposal.objects.create(
            partner=appointment.partner,
            appointment=appointment,
            type=proposal_type,
            status=AppointmentProposal.Status.PENDING,
            created_by=actor,
            expires_at=expires_at,
            message=message,
            reason=reason,
        )
        for index, option_input in enumerate(options):
            _create_proposal_option(
                proposal=proposal,
                appointment=appointment,
                option_input=option_input,
                default_sort_order=index,
            )
        record_audit_event(
            partner=appointment.partner,
            actor=actor,
            event_type="appointment.proposal_created",
            target=proposal,
            message="Appointment change proposal created.",
            payload={
                "appointment_id": str(appointment.id),
                "proposal_type": proposal_type,
                "option_count": len(options),
                "expires_at": expires_at.isoformat(),
            },
        )
        if actor is None:
            _on_commit_schedule_master_client_reschedule_notification(proposal.id)
        else:
            _on_commit_schedule_client_proposal_notification(proposal.id)
    return proposal


def accept_appointment_proposal_option(
    *,
    client_profile: ClientProfile,
    option: AppointmentProposalOption,
    now: datetime | None = None,
) -> Appointment:
    """Accept a pending proposal option from the appointment client's bot context."""

    return _accept_appointment_proposal_option(
        option=option,
        client_profile=client_profile,
        actor=None,
        now=now,
    )


def accept_appointment_proposal_option_as_partner(
    *,
    actor: User,
    option: AppointmentProposalOption,
    now: datetime | None = None,
) -> Appointment:
    """Accept a pending proposal option from the partner admin context."""

    return _accept_appointment_proposal_option(
        option=option,
        client_profile=None,
        actor=actor,
        now=now,
    )


def decline_appointment_proposal(
    *,
    client_profile: ClientProfile,
    proposal: AppointmentProposal,
    now: datetime | None = None,
) -> AppointmentProposal:
    """Decline a pending proposal from the appointment client's bot context."""

    now = now or timezone.now()
    with transaction.atomic():
        proposal = (
            AppointmentProposal.objects.select_for_update()
            .select_related("appointment", "appointment__client_profile")
            .get(id=proposal.id)
        )
        if proposal.appointment.client_profile_id != client_profile.id:
            raise ValidationError({"proposal": "Proposal does not belong to this client."})
        if proposal.created_by_id is None:
            raise ValidationError({"proposal": "A client cannot decline their own request."})
        _validate_pending_proposal(proposal=proposal, now=now)
        proposal.status = AppointmentProposal.Status.DECLINED
        proposal.responded_at = now
        proposal.save(update_fields=["status", "responded_at", "updated_at"])
        record_audit_event(
            partner=proposal.partner,
            actor=None,
            event_type="appointment.proposal_declined",
            target=proposal,
            message="Appointment change proposal declined by client.",
            payload={"appointment_id": str(proposal.appointment_id)},
        )
        _on_commit_schedule_proposal_response_notification(proposal.id)
    return proposal


def decline_appointment_proposal_as_partner(
    *,
    actor: User,
    proposal: AppointmentProposal,
    now: datetime | None = None,
) -> AppointmentProposal:
    """Decline a client-created reschedule request from the partner admin."""

    now = now or timezone.now()
    with transaction.atomic():
        proposal = (
            AppointmentProposal.objects.select_for_update()
            .select_related("partner", "appointment")
            .get(id=proposal.id)
        )
        ensure_partner_access(actor, proposal.partner)
        if proposal.created_by_id is not None:
            raise ValidationError({"proposal": "Only a client request can be declined here."})
        _validate_pending_proposal(proposal=proposal, now=now)
        proposal.status = AppointmentProposal.Status.DECLINED
        proposal.responded_at = now
        proposal.save(update_fields=["status", "responded_at", "updated_at"])
        record_audit_event(
            partner=proposal.partner,
            actor=actor,
            event_type="appointment.client_reschedule_declined",
            target=proposal,
            message="Client reschedule request declined by partner.",
            payload={"appointment_id": str(proposal.appointment_id)},
        )
        _on_commit_schedule_proposal_response_notification(proposal.id)
    return proposal


def cancel_appointment_proposal(
    *,
    actor: User,
    proposal: AppointmentProposal,
    now: datetime | None = None,
) -> AppointmentProposal:
    """Cancel a pending proposal from the partner admin context."""

    now = now or timezone.now()
    with transaction.atomic():
        proposal = (
            AppointmentProposal.objects.select_for_update()
            .select_related("partner")
            .get(id=proposal.id)
        )
        ensure_partner_access(actor, proposal.partner)
        _validate_pending_proposal(proposal=proposal, now=now)
        proposal.status = AppointmentProposal.Status.CANCELLED
        proposal.responded_at = now
        proposal.save(update_fields=["status", "responded_at", "updated_at"])
        record_audit_event(
            partner=proposal.partner,
            actor=actor,
            event_type="appointment.proposal_cancelled",
            target=proposal,
            message="Appointment change proposal cancelled by partner.",
            payload={"appointment_id": str(proposal.appointment_id)},
        )
    return proposal


def expire_pending_appointment_proposals(
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> int:
    """Expire due proposals and cancel requested appointments when approved rules require it."""

    now = now or timezone.now()
    proposal_ids = list(
        AppointmentProposal.objects.filter(
            status=AppointmentProposal.Status.PENDING,
            expires_at__lte=now,
        )
        .order_by("expires_at")
        .values_list("id", flat=True)[:limit]
    )
    expired_count = 0
    for proposal_id in proposal_ids:
        with transaction.atomic():
            proposal = (
                AppointmentProposal.objects.select_for_update()
                .select_related("appointment", "appointment__booking_resource")
                .get(id=proposal_id)
            )
            if proposal.status != AppointmentProposal.Status.PENDING or proposal.expires_at > now:
                continue
            appointment = Appointment.objects.select_for_update().get(id=proposal.appointment_id)
            proposal.status = AppointmentProposal.Status.EXPIRED
            proposal.responded_at = now
            proposal.save(update_fields=["status", "responded_at", "updated_at"])
            expired_count += 1

            appointment_cancelled = False
            if (
                proposal.type == AppointmentProposal.Type.REQUESTED_ALTERNATIVES
                and appointment.status == Appointment.Status.REQUESTED
            ):
                appointment.status = Appointment.Status.CANCELLED_BY_MASTER
                appointment.client_visible_reason = proposal.reason or "Proposal expired."
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
                stale_appointment_notifications_after_commit(appointment.id)
                appointment_cancelled = True
            record_audit_event(
                partner=proposal.partner,
                actor=None,
                event_type="appointment.proposal_expired",
                target=proposal,
                message="Appointment change proposal expired.",
                payload={
                    "appointment_id": str(appointment.id),
                    "appointment_cancelled": appointment_cancelled,
                },
            )
            _on_commit_schedule_expired_proposal_notifications(proposal.id)
    return expired_count


def _accept_appointment_proposal_option(
    *,
    option: AppointmentProposalOption,
    client_profile: ClientProfile | None,
    actor: User | None,
    now: datetime | None,
) -> Appointment:
    now = now or timezone.now()
    with transaction.atomic():
        option = (
            AppointmentProposalOption.objects.select_for_update()
            .select_related("proposal", "proposal__partner")
            .prefetch_related("service_items")
            .get(id=option.id)
        )
        proposal = (
            AppointmentProposal.objects.select_for_update()
            .select_related("appointment", "appointment__booking_resource")
            .get(id=option.proposal_id)
        )
        appointment = (
            Appointment.objects.select_for_update()
            .select_related("partner", "booking_resource", "client_profile")
            .get(id=proposal.appointment_id)
        )
        if client_profile is not None and appointment.client_profile_id != client_profile.id:
            raise ValidationError({"proposal": "Proposal does not belong to this client."})
        if client_profile is not None and proposal.created_by_id is None:
            raise ValidationError({"proposal": "A client cannot accept their own request."})
        if actor is not None:
            ensure_partner_access(actor, proposal.partner)
            if proposal.created_by_id is not None:
                raise ValidationError(
                    {"proposal": "Only a client-created request can be accepted here."}
                )
        _validate_pending_proposal(proposal=proposal, now=now)
        _validate_acceptance_status(appointment=appointment, proposal=proposal)

        start_at, end_at = _candidate_interval(appointment=appointment, option=option)
        acquire_booking_resource_advisory_lock_if_supported(appointment.booking_resource)
        _raise_if_accepted_option_overlaps(
            appointment=appointment,
            start_at=start_at,
            end_at=end_at,
        )

        _apply_option_to_appointment(
            appointment=appointment,
            proposal=proposal,
            option=option,
            start_at=start_at,
            end_at=end_at,
            changed_at=now,
        )
        option.selected_at = now
        option.save(update_fields=["selected_at"])
        proposal.status = AppointmentProposal.Status.ACCEPTED
        proposal.responded_at = now
        proposal.selected_option = option
        proposal.save(update_fields=["status", "responded_at", "selected_option", "updated_at"])
        record_audit_event(
            partner=appointment.partner,
            actor=actor,
            event_type="appointment.proposal_accepted",
            target=proposal,
            message="Appointment change proposal accepted.",
            payload={
                "appointment_id": str(appointment.id),
                "selected_option_id": str(option.id),
            },
        )
        refresh_confirmed_notifications_after_commit(appointment.id)
        _on_commit_schedule_proposal_response_notification(proposal.id)
    return appointment


def _create_proposal_option(
    *,
    proposal: AppointmentProposal,
    appointment: Appointment,
    option_input: AppointmentProposalOptionInput,
    default_sort_order: int,
) -> AppointmentProposalOption:
    snapshot_items = _proposal_option_snapshots(
        appointment=appointment,
        option_input=option_input,
    )
    total_duration_minutes = _snapshot_duration_minutes(snapshot_items)
    service_total_amount = _snapshot_service_total(snapshot_items)
    penalties_total_amount = (
        appointment.penalties_total_amount
        if option_input.penalties_total_amount is None
        else option_input.penalties_total_amount
    )
    if penalties_total_amount < 0:
        raise ValidationError({"penalties_total_amount": "Penalties total cannot be negative."})

    start_at = option_input.start_at
    end_at = None
    if start_at is not None:
        _validate_aware_datetime(start_at, "start_at")
        end_at = start_at + timedelta(minutes=total_duration_minutes)

    option = AppointmentProposalOption.objects.create(
        proposal=proposal,
        start_at=start_at,
        end_at=end_at,
        total_duration_minutes=total_duration_minutes,
        service_total_amount=service_total_amount,
        penalties_total_amount=penalties_total_amount,
        total_amount=service_total_amount + penalties_total_amount,
        currency=_snapshot_currency(snapshot_items),
        is_near_fit=option_input.is_near_fit,
        sort_order=option_input.sort_order or default_sort_order,
    )
    if option_input.service_items is not None:
        AppointmentProposalOptionItem.objects.bulk_create(
            [AppointmentProposalOptionItem(option=option, **item) for item in snapshot_items]
        )
    return option


def _proposal_option_snapshots(
    *,
    appointment: Appointment,
    option_input: AppointmentProposalOptionInput,
) -> tuple[dict, ...]:
    if option_input.service_items is not None:
        return build_service_item_snapshots(
            partner=appointment.partner,
            service_items=option_input.service_items,
        )
    return tuple(_appointment_item_snapshot(item) for item in appointment.service_items.all())


def _appointment_item_snapshot(item) -> dict:
    return {
        "service": item.service,
        "category_name_snapshot": item.category_name_snapshot,
        "service_name_snapshot": item.service_name_snapshot,
        "description_snapshot": item.description_snapshot,
        "price_amount": item.price_amount,
        "currency": item.currency,
        "duration_minutes": item.duration_minutes,
        "is_custom": item.is_custom,
        "is_overridden": item.is_overridden,
        "sort_order": item.sort_order,
    }


def _option_item_snapshot(item: AppointmentProposalOptionItem) -> dict:
    return {
        "service": item.service,
        "category_name_snapshot": item.category_name_snapshot,
        "service_name_snapshot": item.service_name_snapshot,
        "description_snapshot": item.description_snapshot,
        "price_amount": item.price_amount,
        "currency": item.currency,
        "duration_minutes": item.duration_minutes,
        "is_custom": item.is_custom,
        "is_overridden": item.is_overridden,
        "sort_order": item.sort_order,
    }


def _apply_option_to_appointment(
    *,
    appointment: Appointment,
    proposal: AppointmentProposal,
    option: AppointmentProposalOption,
    start_at: datetime,
    end_at: datetime,
    changed_at: datetime,
) -> None:
    update_fields = [
        "start_at",
        "end_at",
        "source",
        "service_total_amount",
        "penalties_total_amount",
        "total_amount",
        "currency",
        "updated_at",
    ]
    appointment.start_at = start_at
    appointment.end_at = end_at
    appointment.source = Appointment.Source.PROPOSAL
    appointment.service_total_amount = option.service_total_amount
    appointment.penalties_total_amount = option.penalties_total_amount
    appointment.total_amount = option.total_amount
    appointment.currency = option.currency
    appointment.is_near_fit = option.is_near_fit
    update_fields.append("is_near_fit")

    if proposal.type == AppointmentProposal.Type.REQUESTED_ALTERNATIVES:
        appointment.status = Appointment.Status.CONFIRMED
        appointment.confirmed_at = changed_at
        appointment.status_changed_at = changed_at
        update_fields.extend(["status", "confirmed_at", "status_changed_at"])

    appointment.save(update_fields=update_fields)
    option_items = tuple(option.service_items.all())
    if option_items:
        replace_appointment_service_items_from_snapshots(
            appointment=appointment,
            snapshot_items=tuple(_option_item_snapshot(item) for item in option_items),
        )


def _candidate_interval(
    *,
    appointment: Appointment,
    option: AppointmentProposalOption,
) -> tuple[datetime, datetime]:
    if option.start_at is not None and option.end_at is not None:
        return option.start_at, option.end_at
    start_at = appointment.start_at
    return start_at, start_at + timedelta(minutes=option.total_duration_minutes)


def _raise_if_accepted_option_overlaps(
    *,
    appointment: Appointment,
    start_at: datetime,
    end_at: datetime,
) -> None:
    exclude_appointment = (
        appointment if appointment.status == Appointment.Status.CONFIRMED else None
    )
    if confirmed_appointments_overlapping(
        booking_resource=appointment.booking_resource,
        start_at=start_at,
        end_at=end_at,
        exclude_appointment=exclude_appointment,
    ).exists():
        raise ValidationError({"start_at": "This time is already occupied."})


def _validate_proposal_type_for_appointment(
    *,
    appointment: Appointment,
    proposal_type: str,
) -> None:
    if (
        proposal_type == AppointmentProposal.Type.REQUESTED_ALTERNATIVES
        and appointment.status != Appointment.Status.REQUESTED
    ):
        raise ValidationError(
            {"appointment": "Requested alternatives require a requested appointment."}
        )
    if (
        proposal_type
        in {
            AppointmentProposal.Type.RESCHEDULE_CONFIRMED,
            AppointmentProposal.Type.APPOINTMENT_CHANGE,
        }
        and appointment.status != Appointment.Status.CONFIRMED
    ):
        raise ValidationError(
            {"appointment": "This proposal type requires a confirmed appointment."}
        )


def _validate_acceptance_status(
    *,
    appointment: Appointment,
    proposal: AppointmentProposal,
) -> None:
    if proposal.type == AppointmentProposal.Type.REQUESTED_ALTERNATIVES:
        if appointment.status != Appointment.Status.REQUESTED:
            raise ValidationError({"appointment": "Appointment is no longer requested."})
        return
    if appointment.status != Appointment.Status.CONFIRMED:
        raise ValidationError({"appointment": "Appointment is no longer confirmed."})


def _validate_pending_proposal(
    *,
    proposal: AppointmentProposal,
    now: datetime,
) -> None:
    if proposal.status != AppointmentProposal.Status.PENDING:
        raise ValidationError({"proposal": "Proposal is not pending."})
    if proposal.expires_at <= now:
        raise ValidationError({"proposal": "Proposal is expired."})


def _validate_aware_datetime(value: datetime, field_name: str) -> None:
    if timezone.is_naive(value):
        raise ValidationError({field_name: "Datetime must be timezone-aware."})


def _snapshot_duration_minutes(snapshot_items: Sequence[dict]) -> int:
    return sum(item["duration_minutes"] for item in snapshot_items)


def _snapshot_service_total(snapshot_items: Sequence[dict]) -> int:
    return sum(item["price_amount"] for item in snapshot_items)


def _snapshot_currency(snapshot_items: Sequence[dict]) -> str:
    currencies = {item["currency"] for item in snapshot_items}
    if len(currencies) != 1:
        raise ValidationError({"currency": "All service items must use one currency."})
    return currencies.pop()


def _on_commit_schedule_client_proposal_notification(proposal_id) -> None:
    transaction.on_commit(lambda: _schedule_client_proposal_notification(proposal_id))


def _on_commit_schedule_master_client_reschedule_notification(proposal_id) -> None:
    transaction.on_commit(lambda: _schedule_master_client_reschedule_notification(proposal_id))


def _on_commit_schedule_proposal_response_notification(proposal_id) -> None:
    transaction.on_commit(lambda: _schedule_proposal_response_notification(proposal_id))


def _on_commit_schedule_expired_proposal_notifications(proposal_id) -> None:
    transaction.on_commit(lambda: _schedule_expired_proposal_notifications(proposal_id))


def _schedule_client_proposal_notification(proposal_id) -> None:
    from apps.notifications.services import schedule_client_appointment_proposal_notification

    proposal = (
        AppointmentProposal.objects.select_related(
            "partner",
            "appointment",
            "appointment__client_profile",
        )
        .prefetch_related("options", "appointment__service_items")
        .get(id=proposal_id)
    )
    schedule_client_appointment_proposal_notification(proposal=proposal)


def _schedule_master_client_reschedule_notification(proposal_id) -> None:
    from apps.notifications.services import schedule_master_client_reschedule_notification

    proposal = (
        AppointmentProposal.objects.select_related(
            "partner",
            "appointment",
            "appointment__client_profile",
        )
        .prefetch_related("options", "appointment__service_items")
        .get(id=proposal_id)
    )
    schedule_master_client_reschedule_notification(
        appointment=proposal.appointment,
        proposal=proposal,
    )


def _schedule_proposal_response_notification(proposal_id) -> None:
    from apps.notifications.services import (
        schedule_client_proposal_response_notification,
        schedule_master_proposal_response_notification,
    )

    proposal = AppointmentProposal.objects.select_related(
        "partner",
        "appointment",
        "appointment__client_profile",
        "selected_option",
        "created_by",
    ).get(id=proposal_id)
    if proposal.created_by_id is None:
        schedule_client_proposal_response_notification(proposal=proposal)
        return
    schedule_master_proposal_response_notification(proposal=proposal)


def _schedule_expired_proposal_notifications(proposal_id) -> None:
    from apps.notifications.services import schedule_expired_proposal_notifications

    proposal = AppointmentProposal.objects.select_related(
        "partner",
        "appointment",
        "appointment__client_profile",
    ).get(id=proposal_id)
    schedule_expired_proposal_notifications(proposal=proposal)
