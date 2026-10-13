from __future__ import annotations

from celery import shared_task

from apps.audit.models import PlatformDiagnostic
from apps.audit.services import (
    cleanup_expired_retained_records,
    record_worker_heartbeat,
    refresh_worker_heartbeat_statuses,
)


@shared_task(name="audit.cleanup_expired_retained_records", queue="maintenance")
def cleanup_expired_retained_records_task() -> int:
    """Delete expired cleanup-eligible records from the approved allowlist."""

    return cleanup_expired_retained_records().total_deleted


@shared_task(name="audit.refresh_worker_health", queue="maintenance")
def refresh_worker_health_task() -> int:
    """Recompute current worker health statuses from stale heartbeat thresholds."""

    return refresh_worker_heartbeat_statuses()


@shared_task(name="audit.heartbeat", queue="maintenance")
def heartbeat_task(component: str = PlatformDiagnostic.Component.DEFAULT_WORKER) -> str:
    """Record a simple heartbeat for low-priority maintenance/default worker checks."""

    heartbeat = record_worker_heartbeat(component=component)
    return str(heartbeat.id)
