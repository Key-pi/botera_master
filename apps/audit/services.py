from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.audit.models import (
    AUDIT_RETAIN_DAYS,
    DIAGNOSTIC_RETAIN_DAYS,
    AuditEvent,
    PlatformDiagnostic,
    WorkerHeartbeat,
)
from core.security.masking import mask_sensitive_text, redact_diagnostic_value

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.partners.models import Partner


HEALTH_THRESHOLDS = {
    PlatformDiagnostic.Component.CELERY_BEAT: (timedelta(minutes=3), timedelta(minutes=5)),
    PlatformDiagnostic.Component.NOTIFICATIONS_WORKER: (
        timedelta(minutes=3),
        timedelta(minutes=5),
    ),
    PlatformDiagnostic.Component.BROADCAST_WORKER: (
        timedelta(minutes=10),
        timedelta(minutes=30),
    ),
    PlatformDiagnostic.Component.DEFAULT_WORKER: (timedelta(minutes=3), timedelta(minutes=5)),
}
DEFAULT_HEALTH_THRESHOLDS = (timedelta(minutes=3), timedelta(minutes=5))


@dataclass(frozen=True, slots=True)
class CleanupResult:
    """Counts of cleanup-eligible rows deleted by one maintenance pass."""

    deleted_by_model: dict[str, int]

    @property
    def total_deleted(self) -> int:
        return sum(self.deleted_by_model.values())


def record_audit_event(
    *,
    partner: Partner,
    event_type: str,
    actor: User | None = None,
    target: object | None = None,
    target_type: str = "",
    target_id: str = "",
    message: str = "",
    payload: dict[str, Any] | None = None,
    reason: str = "",
    retain_days: int = AUDIT_RETAIN_DAYS,
) -> AuditEvent:
    """Create a business-friendly partner-visible audit event."""

    cleaned_event_type = event_type.strip()
    if not cleaned_event_type:
        raise ValidationError({"event_type": "Audit event type is required."})

    resolved_target_type, resolved_target_id = _target_identity(
        target=target,
        target_type=target_type,
        target_id=target_id,
    )
    now = timezone.now()
    return AuditEvent.objects.create(
        partner=partner,
        actor=actor,
        event_type=cleaned_event_type,
        target_type=resolved_target_type,
        target_id=resolved_target_id,
        message=mask_sensitive_text(message),
        payload_masked=redact_diagnostic_value(payload or {}),
        reason=mask_sensitive_text(reason),
        retain_until=now + timedelta(days=retain_days),
    )


def record_platform_diagnostic(
    *,
    code: str,
    severity: str,
    component: str,
    message: str = "",
    metadata: dict[str, Any] | None = None,
    partner: Partner | None = None,
    queue: str = "",
    worker: str = "",
    target: object | None = None,
    target_type: str = "",
    target_id: str = "",
    retain_days: int = DIAGNOSTIC_RETAIN_DAYS,
) -> PlatformDiagnostic:
    """Persist a platform-only diagnostic with sensitive metadata redacted."""

    cleaned_code = code.strip()
    if not cleaned_code:
        raise ValidationError({"code": "Diagnostic code is required."})
    if severity not in PlatformDiagnostic.Severity.values:
        raise ValidationError({"severity": "Unsupported diagnostic severity."})
    if component not in PlatformDiagnostic.Component.values:
        raise ValidationError({"component": "Unsupported diagnostic component."})

    resolved_target_type, resolved_target_id = _target_identity(
        target=target,
        target_type=target_type,
        target_id=target_id,
    )
    now = timezone.now()
    return PlatformDiagnostic.objects.create(
        partner=partner,
        component=component,
        code=cleaned_code,
        severity=severity,
        message=mask_sensitive_text(message),
        metadata=redact_diagnostic_value(metadata or {}),
        queue=queue.strip(),
        worker=worker.strip(),
        target_type=resolved_target_type,
        target_id=resolved_target_id,
        retain_until=now + timedelta(days=retain_days),
    )


def mark_diagnostic_resolved(diagnostic: PlatformDiagnostic) -> PlatformDiagnostic:
    """Mark a diagnostic resolved without deleting its retained history."""

    diagnostic.resolved_at = timezone.now()
    diagnostic.save(update_fields=["resolved_at", "updated_at"])
    return diagnostic


def record_worker_heartbeat(
    *,
    component: str,
    queue: str = "",
    worker: str = "",
    metadata: dict[str, Any] | None = None,
    seen_at=None,
) -> WorkerHeartbeat:
    """Update current worker/component health after a successful ping."""

    current_time = seen_at or timezone.now()
    if component not in PlatformDiagnostic.Component.values:
        raise ValidationError({"component": "Unsupported diagnostic component."})

    heartbeat, _created = WorkerHeartbeat.objects.update_or_create(
        component=component,
        queue=queue.strip(),
        worker=worker.strip(),
        defaults={
            "status": WorkerHeartbeat.Status.OK,
            "last_seen_at": current_time,
            "last_success_at": current_time,
            "last_error_code": "",
            "metadata": redact_diagnostic_value(metadata or {}),
        },
    )
    return heartbeat


def record_worker_error(
    *,
    component: str,
    code: str,
    queue: str = "",
    worker: str = "",
    metadata: dict[str, Any] | None = None,
    error_at=None,
) -> WorkerHeartbeat:
    """Update current worker/component health after an observed error."""

    current_time = error_at or timezone.now()
    if component not in PlatformDiagnostic.Component.values:
        raise ValidationError({"component": "Unsupported diagnostic component."})
    heartbeat, _created = WorkerHeartbeat.objects.update_or_create(
        component=component,
        queue=queue.strip(),
        worker=worker.strip(),
        defaults={
            "status": WorkerHeartbeat.Status.CRITICAL,
            "last_seen_at": current_time,
            "last_error_at": current_time,
            "last_error_code": code[:128],
            "metadata": redact_diagnostic_value(metadata or {}),
        },
    )
    return heartbeat


def evaluate_worker_heartbeat(
    heartbeat: WorkerHeartbeat,
    *,
    now=None,
) -> str:
    """Map current heartbeat freshness into OK / Warning / Critical."""

    current_time = now or timezone.now()
    if heartbeat.last_error_at and (
        not heartbeat.last_success_at or heartbeat.last_error_at > heartbeat.last_success_at
    ):
        return WorkerHeartbeat.Status.CRITICAL
    if heartbeat.last_seen_at is None:
        return WorkerHeartbeat.Status.CRITICAL

    warning_after, critical_after = HEALTH_THRESHOLDS.get(
        heartbeat.component,
        DEFAULT_HEALTH_THRESHOLDS,
    )
    stale_for = current_time - heartbeat.last_seen_at
    if stale_for >= critical_after:
        return WorkerHeartbeat.Status.CRITICAL
    if stale_for >= warning_after:
        return WorkerHeartbeat.Status.WARNING
    return WorkerHeartbeat.Status.OK


def refresh_worker_heartbeat_statuses(*, now=None) -> int:
    """Recalculate current heartbeat statuses from approved MVP thresholds."""

    current_time = now or timezone.now()
    updated = 0
    for heartbeat in WorkerHeartbeat.objects.all():
        status = evaluate_worker_heartbeat(heartbeat, now=current_time)
        if heartbeat.status != status:
            heartbeat.status = status
            heartbeat.save(update_fields=["status", "updated_at"])
            updated += 1
    return updated


def cleanup_expired_retained_records(*, now=None) -> CleanupResult:
    """Delete expired cleanup-eligible records from the approved allowlist only."""

    current_time = now or timezone.now()
    deleted_by_model: dict[str, int] = {}
    with transaction.atomic():
        for model in _cleanup_eligible_models():
            queryset = model.objects.filter(
                retain_until__isnull=False,
                retain_until__lte=current_time,
            )
            count = queryset.count()
            if not count:
                continue
            queryset.delete()
            deleted_by_model[_model_label(model)] = count

    record_platform_diagnostic(
        component=PlatformDiagnostic.Component.CLEANUP,
        severity=PlatformDiagnostic.Severity.INFO,
        code="cleanup.deleted_records",
        message="Cleanup deleted expired retained records.",
        metadata={
            "deleted_by_model": deleted_by_model,
            "total_deleted": sum(deleted_by_model.values()),
        },
    )
    return CleanupResult(deleted_by_model=deleted_by_model)


def _cleanup_eligible_models():
    """Return explicit cleanup allowlist; never infer from arbitrary business models."""

    return (
        apps.get_model("audit", "AuditEvent"),
        apps.get_model("audit", "PlatformDiagnostic"),
        apps.get_model("notifications", "DeliveryAttempt"),
        apps.get_model("broadcasts", "BroadcastRecipient"),
        apps.get_model("integrations", "IntegrationEvent"),
    )


def _target_identity(
    *,
    target: object | None,
    target_type: str,
    target_id: str,
) -> tuple[str, str]:
    if target is None:
        return target_type.strip(), str(target_id).strip()
    resolved_type = target_type.strip() or target.__class__.__name__
    resolved_id = target_id.strip() if target_id else str(getattr(target, "id", ""))
    return resolved_type, resolved_id


def _model_label(model) -> str:
    return f"{model._meta.app_label}.{model._meta.model_name}"
