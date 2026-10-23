from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.accounts.services import ensure_partner_access
from apps.appointments.models import Appointment
from apps.audit.services import record_audit_event
from apps.catalog.models import DEFAULT_CURRENCY
from apps.finance.models import Expense, PartnerPenaltySettings, Penalty, PenaltyApplication
from apps.schedule.models import PartnerScheduleSettings

if TYPE_CHECKING:
    from datetime import date

    from apps.accounts.models import User
    from apps.clients.models import ClientProfile
    from apps.partners.models import Partner


APPLICATION_TOTAL_STATUSES = (
    PenaltyApplication.Status.ACTIVE,
    PenaltyApplication.Status.SETTLED,
)


def get_or_create_penalty_settings(*, partner: Partner) -> PartnerPenaltySettings:
    """Return partner penalty settings, creating default disabled settings if needed."""

    settings, _created = PartnerPenaltySettings.objects.get_or_create(partner=partner)
    return settings


def update_penalty_settings(
    *,
    actor: User,
    partner: Partner,
    penalties_enabled: bool | None = None,
    late_cancel_penalty_amount: int | None = None,
    no_show_penalty_amount: int | None = None,
    late_arrival_threshold_minutes: int | None = None,
    late_arrival_penalty_amount: int | None = None,
) -> PartnerPenaltySettings:
    """Update partner penalty behavior through explicit validation."""

    ensure_partner_access(actor, partner)
    with transaction.atomic():
        settings, _created = PartnerPenaltySettings.objects.select_for_update().get_or_create(
            partner=partner
        )
        if penalties_enabled is not None:
            settings.penalties_enabled = bool(penalties_enabled)
        if late_cancel_penalty_amount is not None:
            _validate_non_negative_money(late_cancel_penalty_amount, "late_cancel_penalty_amount")
            settings.late_cancel_penalty_amount = late_cancel_penalty_amount
        if no_show_penalty_amount is not None:
            _validate_non_negative_money(no_show_penalty_amount, "no_show_penalty_amount")
            settings.no_show_penalty_amount = no_show_penalty_amount
        if late_arrival_threshold_minutes is not None:
            if late_arrival_threshold_minutes <= 0:
                raise ValidationError(
                    {"late_arrival_threshold_minutes": "Late-arrival threshold must be positive."}
                )
            settings.late_arrival_threshold_minutes = late_arrival_threshold_minutes
        if late_arrival_penalty_amount is not None:
            _validate_non_negative_money(
                late_arrival_penalty_amount,
                "late_arrival_penalty_amount",
            )
            settings.late_arrival_penalty_amount = late_arrival_penalty_amount
        settings.currency = DEFAULT_CURRENCY
        settings.save()
        record_audit_event(
            partner=partner,
            actor=actor,
            event_type="penalty.settings_updated",
            target=settings,
            message="Penalty settings updated.",
            payload={
                "penalties_enabled": settings.penalties_enabled,
                "late_cancel_penalty_amount": settings.late_cancel_penalty_amount,
                "no_show_penalty_amount": settings.no_show_penalty_amount,
                "late_arrival_threshold_minutes": settings.late_arrival_threshold_minutes,
                "late_arrival_penalty_amount": settings.late_arrival_penalty_amount,
                "currency": settings.currency,
            },
        )
    return settings


def create_penalty(
    *,
    actor: User | None,
    partner: Partner,
    client_profile: ClientProfile,
    reason: str,
    amount: int,
    source_appointment: Appointment | None = None,
    late_by_minutes: int | None = None,
    note: str = "",
    currency: str = DEFAULT_CURRENCY,
) -> Penalty:
    """Create a partner-scoped client penalty/debt."""

    _ensure_actor_or_runtime_access(actor=actor, partner=partner)
    _validate_penalty_reason(reason)
    _validate_non_negative_money(amount, "amount")
    currency = _normalize_currency(currency)
    if client_profile.partner_id != partner.id:
        raise ValidationError({"client_profile": "Client must belong to this partner."})
    if source_appointment is not None:
        _validate_appointment_scope(
            partner=partner,
            appointment=source_appointment,
            client_profile=client_profile,
        )
    if late_by_minutes is not None:
        if reason != Penalty.Reason.LATE_ARRIVAL:
            raise ValidationError(
                {"late_by_minutes": "Late minutes are only valid for late-arrival penalties."}
            )
        if late_by_minutes <= 0:
            raise ValidationError({"late_by_minutes": "Late minutes must be positive."})

    with transaction.atomic():
        if source_appointment is not None:
            source_appointment = Appointment.objects.select_for_update().get(
                id=source_appointment.id
            )
            _validate_appointment_scope(
                partner=partner,
                appointment=source_appointment,
                client_profile=client_profile,
            )
            if Penalty.objects.filter(
                source_appointment=source_appointment,
                reason=reason,
            ).exists():
                raise ValidationError(
                    {"penalty": "This appointment already has a penalty for this reason."}
                )
        penalty = Penalty.objects.create(
            partner=partner,
            client_profile=client_profile,
            source_appointment=source_appointment,
            reason=reason,
            amount=amount,
            currency=currency,
            late_by_minutes=late_by_minutes,
            note=note,
            created_by=actor,
        )
        _record_penalty_audit(
            penalty=penalty,
            actor=actor,
            event_type="penalty.created",
            message="Penalty created.",
        )
        _on_commit_schedule_penalty_notification(penalty_id=penalty.id, event="created")
    return penalty


def create_late_cancel_penalty_for_appointment(
    *,
    actor: User | None,
    appointment: Appointment,
    note: str = "",
) -> Penalty:
    """Create the configured late-cancellation penalty for an appointment."""

    settings = _penalty_settings_enabled(appointment.partner)
    if appointment.status != Appointment.Status.CANCELLED_BY_CLIENT:
        raise ValidationError(
            {"appointment": "Late-cancel penalty requires a client-cancelled appointment."}
        )
    if not is_late_cancellation(appointment=appointment):
        raise ValidationError({"appointment": "Appointment was not cancelled late."})
    return create_penalty(
        actor=actor,
        partner=appointment.partner,
        client_profile=appointment.client_profile,
        source_appointment=appointment,
        reason=Penalty.Reason.LATE_CANCEL,
        amount=settings.late_cancel_penalty_amount,
        currency=settings.currency,
        note=note,
    )


def create_no_show_penalty_for_appointment(
    *,
    actor: User,
    appointment: Appointment,
    note: str = "",
) -> Penalty:
    """Create the configured no-show penalty after the master marks no-show."""

    ensure_partner_access(actor, appointment.partner)
    settings = _penalty_settings_enabled(appointment.partner)
    if appointment.status != Appointment.Status.NO_SHOW:
        raise ValidationError({"appointment": "No-show penalty requires no-show status."})
    return create_penalty(
        actor=actor,
        partner=appointment.partner,
        client_profile=appointment.client_profile,
        source_appointment=appointment,
        reason=Penalty.Reason.NO_SHOW,
        amount=settings.no_show_penalty_amount,
        currency=settings.currency,
        note=note,
    )


def create_late_arrival_penalty_for_appointment(
    *,
    actor: User,
    appointment: Appointment,
    late_by_minutes: int,
    note: str = "",
) -> Penalty:
    """Create the configured late-arrival penalty when lateness crosses the threshold."""

    ensure_partner_access(actor, appointment.partner)
    settings = _penalty_settings_enabled(appointment.partner)
    if late_by_minutes < settings.late_arrival_threshold_minutes:
        raise ValidationError({"late_by_minutes": "Late-arrival threshold was not reached."})
    if appointment.status not in {Appointment.Status.CONFIRMED, Appointment.Status.COMPLETED}:
        raise ValidationError(
            {"appointment": "Late-arrival penalty requires confirmed or completed status."}
        )
    if appointment.start_at > timezone.now():
        raise ValidationError(
            {"appointment": "Late arrival can only be recorded after the appointment starts."}
        )
    penalty_note = note or f"Late by {late_by_minutes} minutes."
    return create_penalty(
        actor=actor,
        partner=appointment.partner,
        client_profile=appointment.client_profile,
        source_appointment=appointment,
        reason=Penalty.Reason.LATE_ARRIVAL,
        amount=settings.late_arrival_penalty_amount,
        currency=settings.currency,
        late_by_minutes=late_by_minutes,
        note=penalty_note,
    )


def is_late_cancellation(
    *,
    appointment: Appointment,
    cancelled_at: datetime | None = None,
) -> bool:
    """Return whether cancellation is inside the partner-configured deadline."""

    cancelled_at = cancelled_at or appointment.cancelled_at or timezone.now()
    if timezone.is_naive(cancelled_at):
        raise ValidationError({"cancelled_at": "Datetime must be timezone-aware."})
    settings = PartnerScheduleSettings.objects.filter(partner=appointment.partner).first()
    deadline_hours = settings.cancellation_deadline_hours if settings else 6
    return cancelled_at > appointment.start_at - timedelta(hours=deadline_hours)


def apply_penalties_to_appointment(
    *,
    actor: User,
    appointment: Appointment,
    penalties: Sequence[Penalty],
) -> tuple[PenaltyApplication, ...]:
    """Apply selected unpaid penalties to an appointment total."""

    ensure_partner_access(actor, appointment.partner)
    if not penalties:
        raise ValidationError({"penalties": "At least one penalty is required."})

    penalty_ids = [penalty.id for penalty in penalties]
    with transaction.atomic():
        appointment = Appointment.objects.select_for_update().get(id=appointment.id)
        if appointment.status in {
            Appointment.Status.CANCELLED_BY_CLIENT,
            Appointment.Status.CANCELLED_BY_MASTER,
            Appointment.Status.NO_SHOW,
        }:
            raise ValidationError({"appointment": "Cannot apply penalties to this appointment."})
        locked_penalties = list(
            Penalty.objects.select_for_update().filter(id__in=penalty_ids).order_by("created_at")
        )
        if len(locked_penalties) != len(set(penalty_ids)):
            raise ValidationError({"penalties": "One or more penalties were not found."})

        applications: list[PenaltyApplication] = []
        for penalty in locked_penalties:
            _validate_penalty_can_apply(penalty=penalty, appointment=appointment)
            applications.append(
                PenaltyApplication.objects.create(
                    partner=appointment.partner,
                    penalty=penalty,
                    appointment=appointment,
                    amount_snapshot=penalty.amount,
                    currency=penalty.currency,
                    status=PenaltyApplication.Status.ACTIVE,
                )
            )
            penalty.status = Penalty.Status.APPLIED_TO_APPOINTMENT
            penalty.save(update_fields=["status", "updated_at"])
            _record_penalty_audit(
                penalty=penalty,
                actor=actor,
                event_type="penalty.applied",
                message="Penalty applied to appointment.",
                extra_payload={"appointment_id": str(appointment.id)},
            )

        _recalculate_appointment_penalty_total(appointment=appointment)
    return tuple(applications)


def settle_penalty_application(
    *,
    actor: User,
    application: PenaltyApplication,
) -> PenaltyApplication:
    """Mark an applied penalty as paid through its appointment."""

    ensure_partner_access(actor, application.partner)
    now = timezone.now()
    with transaction.atomic():
        application = (
            PenaltyApplication.objects.select_for_update()
            .select_related("penalty", "appointment")
            .get(id=application.id)
        )
        if application.status != PenaltyApplication.Status.ACTIVE:
            raise ValidationError({"application": "Only active applications can be settled."})
        penalty = Penalty.objects.select_for_update().get(id=application.penalty_id)
        _settle_locked_application(
            application=application,
            penalty=penalty,
            settled_at=now,
            actor=actor,
        )
    return application


def release_penalty_application(
    *,
    actor: User,
    application: PenaltyApplication,
) -> PenaltyApplication:
    """Release an active application and return the penalty to unpaid."""

    ensure_partner_access(actor, application.partner)
    return _release_penalty_application(
        application_id=application.id,
        released_at=timezone.now(),
        actor=actor,
    )


def release_active_penalty_applications_for_appointment(
    *,
    appointment: Appointment,
    released_at: datetime | None = None,
) -> int:
    """Release all still-unsettled penalty applications for a cancelled appointment."""

    released_at = released_at or timezone.now()
    application_ids = list(
        PenaltyApplication.objects.filter(
            appointment=appointment,
            status=PenaltyApplication.Status.ACTIVE,
        ).values_list("id", flat=True)
    )
    released_count = 0
    for application_id in application_ids:
        _release_penalty_application(application_id=application_id, released_at=released_at)
        released_count += 1
    return released_count


def mark_penalty_paid(*, actor: User, penalty: Penalty) -> Penalty:
    """Mark an unpaid penalty paid, or settle its active appointment application."""

    ensure_partner_access(actor, penalty.partner)
    with transaction.atomic():
        penalty = Penalty.objects.select_for_update().get(id=penalty.id)
        if penalty.status == Penalty.Status.PAID:
            return penalty
        if penalty.status == Penalty.Status.WAIVED:
            raise ValidationError({"penalty": "Waived penalties cannot be paid."})
        active_application = (
            PenaltyApplication.objects.select_for_update()
            .filter(penalty=penalty, status=PenaltyApplication.Status.ACTIVE)
            .select_related("appointment")
            .first()
        )
        if active_application is not None:
            _settle_locked_application(
                application=active_application,
                penalty=penalty,
                settled_at=timezone.now(),
                actor=actor,
            )
            return penalty

        penalty.status = Penalty.Status.PAID
        penalty.paid_at = timezone.now()
        penalty.save(update_fields=["status", "paid_at", "updated_at"])
        _record_penalty_audit(
            penalty=penalty,
            actor=actor,
            event_type="penalty.paid",
            message="Penalty marked as paid.",
        )
        _on_commit_schedule_penalty_notification(penalty_id=penalty.id, event="paid")
    return penalty


def waive_penalty(*, actor: User, penalty: Penalty, note: str = "") -> Penalty:
    """Forgive an unpaid/applied penalty and release active appointment applications."""

    ensure_partner_access(actor, penalty.partner)
    now = timezone.now()
    with transaction.atomic():
        penalty = Penalty.objects.select_for_update().get(id=penalty.id)
        if penalty.status == Penalty.Status.PAID:
            raise ValidationError({"penalty": "Paid penalties cannot be waived."})
        if penalty.status == Penalty.Status.WAIVED:
            return penalty
        active_application_ids = list(
            PenaltyApplication.objects.filter(
                penalty=penalty,
                status=PenaltyApplication.Status.ACTIVE,
            ).values_list("id", flat=True)
        )
        for application_id in active_application_ids:
            _release_penalty_application(
                application_id=application_id,
                released_at=now,
                actor=actor,
            )
        penalty.status = Penalty.Status.WAIVED
        penalty.waived_at = now
        if note:
            penalty.note = note
            update_fields = ["status", "waived_at", "note", "updated_at"]
        else:
            update_fields = ["status", "waived_at", "updated_at"]
        penalty.save(update_fields=update_fields)
        _record_penalty_audit(
            penalty=penalty,
            actor=actor,
            event_type="penalty.waived",
            message="Penalty waived.",
            reason=note,
        )
        _on_commit_schedule_penalty_notification(penalty_id=penalty.id, event="waived")
    return penalty


def create_expense(
    *,
    actor: User,
    partner: Partner,
    date: date,
    amount: int,
    description: str,
    currency: str = DEFAULT_CURRENCY,
) -> Expense:
    """Create a manual partner expense for finance reports."""

    ensure_partner_access(actor, partner)
    if amount <= 0:
        raise ValidationError({"amount": "Expense amount must be positive."})
    cleaned_description = description.strip()
    if not cleaned_description:
        raise ValidationError({"description": "Expense description is required."})
    return Expense.objects.create(
        partner=partner,
        date=date,
        amount=amount,
        currency=_normalize_currency(currency),
        description=cleaned_description,
        created_by=actor,
    )


def _release_penalty_application(
    *,
    application_id,
    released_at: datetime,
    actor: User | None = None,
) -> PenaltyApplication:
    with transaction.atomic():
        application = (
            PenaltyApplication.objects.select_for_update()
            .select_related("penalty", "appointment")
            .get(id=application_id)
        )
        if application.status != PenaltyApplication.Status.ACTIVE:
            raise ValidationError({"application": "Only active applications can be released."})
        penalty = Penalty.objects.select_for_update().get(id=application.penalty_id)
        application.status = PenaltyApplication.Status.RELEASED
        application.released_at = released_at
        application.save(update_fields=["status", "released_at", "updated_at"])
        penalty.status = Penalty.Status.UNPAID
        penalty.save(update_fields=["status", "updated_at"])
        _recalculate_appointment_penalty_total(appointment=application.appointment)
        _record_penalty_audit(
            penalty=penalty,
            actor=actor,
            event_type="penalty.released",
            message="Penalty removed from appointment and returned to unpaid.",
            extra_payload={"appointment_id": str(application.appointment_id)},
        )
        return application


def _settle_locked_application(
    *,
    application: PenaltyApplication,
    penalty: Penalty,
    settled_at: datetime,
    actor: User,
) -> None:
    application.status = PenaltyApplication.Status.SETTLED
    application.settled_at = settled_at
    application.save(update_fields=["status", "settled_at", "updated_at"])
    penalty.status = Penalty.Status.PAID
    penalty.paid_at = settled_at
    penalty.save(update_fields=["status", "paid_at", "updated_at"])
    _recalculate_appointment_penalty_total(appointment=application.appointment)
    _record_penalty_audit(
        penalty=penalty,
        actor=actor,
        event_type="penalty.paid",
        message="Applied penalty marked as paid.",
        extra_payload={"appointment_id": str(application.appointment_id)},
    )
    _on_commit_schedule_penalty_notification(penalty_id=penalty.id, event="paid")


def _record_penalty_audit(
    *,
    penalty: Penalty,
    actor: User | None,
    event_type: str,
    message: str,
    reason: str = "",
    extra_payload: dict[str, str] | None = None,
) -> None:
    payload = {
        "client_profile_id": str(penalty.client_profile_id),
        "reason": penalty.reason,
        "status": penalty.status,
        "amount": penalty.amount,
        "currency": penalty.currency,
        **(extra_payload or {}),
    }
    record_audit_event(
        partner=penalty.partner,
        actor=actor,
        event_type=event_type,
        target=penalty,
        message=message,
        payload=payload,
        reason=reason,
    )


def _on_commit_schedule_penalty_notification(*, penalty_id, event: str) -> None:
    transaction.on_commit(
        partial(
            _schedule_penalty_notification_after_commit,
            penalty_id=penalty_id,
            event=event,
        )
    )


def _schedule_penalty_notification_after_commit(*, penalty_id, event: str) -> None:
    from apps.notifications.services import schedule_client_penalty_notification

    penalty = (
        Penalty.objects.select_related(
            "partner",
            "client_profile",
            "client_profile__telegram_identity",
            "source_appointment",
        )
        .filter(id=penalty_id)
        .first()
    )
    if penalty is None:
        return
    schedule_client_penalty_notification(penalty=penalty, event=event)


def _recalculate_appointment_penalty_total(*, appointment: Appointment) -> None:
    penalty_total = (
        PenaltyApplication.objects.filter(
            appointment=appointment,
            status__in=APPLICATION_TOTAL_STATUSES,
        ).aggregate(total=Sum("amount_snapshot"))["total"]
        or 0
    )
    appointment.penalties_total_amount = penalty_total
    appointment.total_amount = appointment.service_total_amount + penalty_total
    appointment.save(update_fields=["penalties_total_amount", "total_amount", "updated_at"])


def _validate_penalty_can_apply(*, penalty: Penalty, appointment: Appointment) -> None:
    if penalty.partner_id != appointment.partner_id:
        raise ValidationError({"penalties": "Penalty must belong to this partner."})
    if penalty.client_profile_id != appointment.client_profile_id:
        raise ValidationError({"penalties": "Penalty must belong to the appointment client."})
    if penalty.status != Penalty.Status.UNPAID:
        raise ValidationError({"penalties": "Only unpaid penalties can be applied."})
    if penalty.currency != appointment.currency:
        raise ValidationError({"currency": "Penalty currency must match appointment currency."})
    if PenaltyApplication.objects.filter(
        penalty=penalty,
        status=PenaltyApplication.Status.ACTIVE,
    ).exists():
        raise ValidationError({"penalties": "Penalty is already applied."})


def _penalty_settings_enabled(partner: Partner) -> PartnerPenaltySettings:
    settings = get_or_create_penalty_settings(partner=partner)
    if not settings.penalties_enabled:
        raise ValidationError({"penalties_enabled": "Penalties are disabled for this partner."})
    return settings


def _validate_appointment_scope(
    *,
    partner: Partner,
    appointment: Appointment,
    client_profile: ClientProfile,
) -> None:
    if appointment.partner_id != partner.id:
        raise ValidationError({"appointment": "Appointment must belong to this partner."})
    if appointment.client_profile_id != client_profile.id:
        raise ValidationError({"appointment": "Appointment must belong to this client."})


def _validate_penalty_reason(reason: str) -> None:
    if reason not in Penalty.Reason.values:
        raise ValidationError({"reason": "Unknown penalty reason."})


def _validate_non_negative_money(value: int, field_name: str) -> None:
    if value < 0:
        raise ValidationError({field_name: "Amount cannot be negative."})


def _normalize_currency(currency: str) -> str:
    normalized_currency = currency.strip().upper()
    if len(normalized_currency) != 3:
        raise ValidationError({"currency": "Currency must be a three-letter code."})
    return normalized_currency


def _ensure_actor_or_runtime_access(*, actor: User | None, partner: Partner) -> None:
    if actor is not None:
        ensure_partner_access(actor, partner)
