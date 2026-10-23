from __future__ import annotations

from django.db.models import QuerySet

from apps.accounts.selectors import partner_ids_for_user
from apps.audit.models import AuditEvent, PlatformDiagnostic, WorkerHeartbeat
from apps.audit.services import evaluate_worker_heartbeat


def audit_events_for_partner(partner) -> QuerySet[AuditEvent]:
    """Return partner-visible activity events for one partner."""

    return AuditEvent.objects.filter(partner=partner).select_related("partner", "actor")


def audit_events_visible_to_user(user) -> QuerySet[AuditEvent]:
    """Return audit events visible through trusted partner memberships."""

    queryset = AuditEvent.objects.select_related("partner", "actor")
    if user.is_superuser:
        return queryset
    return queryset.filter(partner_id__in=partner_ids_for_user(user))


def platform_diagnostics_visible_to_user(user) -> QuerySet[PlatformDiagnostic]:
    """Return platform diagnostics for superusers only."""

    queryset = PlatformDiagnostic.objects.select_related("partner")
    if user.is_superuser:
        return queryset
    return queryset.none()


def active_platform_problems() -> QuerySet[PlatformDiagnostic]:
    """Return unresolved warnings/errors/critical diagnostics for health views."""

    return PlatformDiagnostic.objects.filter(
        resolved_at__isnull=True,
        severity__in=[
            PlatformDiagnostic.Severity.WARNING,
            PlatformDiagnostic.Severity.ERROR,
            PlatformDiagnostic.Severity.CRITICAL,
        ],
    ).select_related("partner")


def worker_heartbeats() -> QuerySet[WorkerHeartbeat]:
    """Return current worker/component health records."""

    return WorkerHeartbeat.objects.all().order_by("component", "queue", "worker")


def worker_health_rows(*, now=None) -> tuple[tuple[WorkerHeartbeat, str], ...]:
    """Return current heartbeats with freshly evaluated health status."""

    return tuple(
        (heartbeat, evaluate_worker_heartbeat(heartbeat, now=now))
        for heartbeat in worker_heartbeats()
    )
