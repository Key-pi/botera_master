from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Count, Min, QuerySet
from django.utils import timezone

from apps.accounts.selectors import partner_ids_for_user
from apps.audit.models import AuditEvent, PlatformDiagnostic, WorkerHeartbeat
from apps.audit.services import evaluate_worker_heartbeat
from apps.broadcasts.models import BroadcastCampaign
from apps.notifications.models import ScheduledNotification

NOTIFICATION_LAG_WARNING_AFTER = timedelta(minutes=2)
NOTIFICATION_LAG_CRITICAL_AFTER = timedelta(minutes=5)
BROADCAST_STALL_WARNING_AFTER = timedelta(minutes=15)
BROADCAST_STALL_CRITICAL_AFTER = timedelta(minutes=60)

EXPECTED_RUNTIME_COMPONENTS = (
    (PlatformDiagnostic.Component.BOT_RUNNER, "Bot runner"),
    (PlatformDiagnostic.Component.CELERY_BEAT, "Celery beat"),
    (PlatformDiagnostic.Component.NOTIFICATIONS_WORKER, "Notifications worker"),
    (PlatformDiagnostic.Component.BROADCAST_WORKER, "Broadcast worker"),
    (PlatformDiagnostic.Component.DEFAULT_WORKER, "Default / maintenance worker"),
)

_STATUS_RANK = {
    WorkerHeartbeat.Status.OK: 0,
    WorkerHeartbeat.Status.WARNING: 1,
    WorkerHeartbeat.Status.CRITICAL: 2,
}


@dataclass(frozen=True, slots=True)
class PlatformHealthCheck:
    """One business-readable platform health signal."""

    key: str
    label: str
    status: str
    summary: str


@dataclass(frozen=True, slots=True)
class ActiveBroadcastHealth:
    """Progress and freshness for one currently dispatching campaign."""

    campaign_id: object
    partner_name: str
    status: str
    health_status: str
    processed_count: int
    total_count: int
    progress_percent: int
    updated_at: object
    progress_age: str


@dataclass(frozen=True, slots=True)
class PlatformHealthSnapshot:
    """Composed read model for the superuser operational dashboard."""

    overall_status: str
    generated_at: object
    checks: tuple[PlatformHealthCheck, ...]
    active_broadcasts: tuple[ActiveBroadcastHealth, ...]
    recent_problems: tuple[PlatformDiagnostic, ...]


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


def platform_health_snapshot(*, now=None) -> PlatformHealthSnapshot:
    """Compose current runtime, delivery, and diagnostic health from durable state.

    The web request deliberately does not ping Redis or external services. Fresh
    scheduled worker heartbeats prove that beat, broker, and workers recently
    completed an end-to-end Celery round trip.
    """

    current_time = now or timezone.now()
    component_heartbeats = {
        heartbeat.component: heartbeat
        for heartbeat in WorkerHeartbeat.objects.filter(
            component__in=[component for component, _label in EXPECTED_RUNTIME_COMPONENTS],
            queue="",
            worker="",
        )
    }
    runtime_checks = tuple(
        _runtime_component_check(
            component=component,
            label=label,
            heartbeat=component_heartbeats.get(component),
            now=current_time,
        )
        for component, label in EXPECTED_RUNTIME_COMPONENTS
    )

    runtime_statuses = {check.key: check.status for check in runtime_checks}
    celery_status = _worst_status(
        runtime_statuses[component]
        for component in (
            PlatformDiagnostic.Component.CELERY_BEAT,
            PlatformDiagnostic.Component.NOTIFICATIONS_WORKER,
            PlatformDiagnostic.Component.BROADCAST_WORKER,
            PlatformDiagnostic.Component.DEFAULT_WORKER,
        )
    )
    infrastructure_checks = (
        PlatformHealthCheck(
            key="web",
            label="Web",
            status=WorkerHeartbeat.Status.OK,
            summary="This authenticated dashboard request completed successfully.",
        ),
        PlatformHealthCheck(
            key="postgres",
            label="PostgreSQL",
            status=WorkerHeartbeat.Status.OK,
            summary="Health dashboard queries completed successfully.",
        ),
        PlatformHealthCheck(
            key="redis",
            label="Redis / Celery broker",
            status=celery_status,
            summary=(
                "Scheduled heartbeat tasks are flowing through the broker."
                if celery_status == WorkerHeartbeat.Status.OK
                else (
                    "Inferred from stale or missing Celery heartbeat tasks; "
                    "inspect Redis and workers."
                )
            ),
        ),
    )

    notification_check = _notification_lag_check(now=current_time)
    broadcast_check, active_broadcasts = _broadcast_progress_check(now=current_time)
    diagnostic_check, recent_problems = _diagnostic_problem_check()
    checks = (
        *runtime_checks,
        *infrastructure_checks,
        notification_check,
        broadcast_check,
        diagnostic_check,
    )
    return PlatformHealthSnapshot(
        overall_status=_worst_status(check.status for check in checks),
        generated_at=current_time,
        checks=checks,
        active_broadcasts=active_broadcasts,
        recent_problems=recent_problems,
    )


def _runtime_component_check(
    *,
    component: str,
    label: str,
    heartbeat: WorkerHeartbeat | None,
    now,
) -> PlatformHealthCheck:
    if heartbeat is None:
        return PlatformHealthCheck(
            key=component,
            label=label,
            status=WorkerHeartbeat.Status.CRITICAL,
            summary="No heartbeat has been recorded.",
        )

    status = evaluate_worker_heartbeat(heartbeat, now=now)
    age = _age_label(now=now, value=heartbeat.last_seen_at)
    summary = f"Last heartbeat {age}."
    if heartbeat.last_error_code and status == WorkerHeartbeat.Status.CRITICAL:
        summary = f"Last heartbeat {age}; error: {heartbeat.last_error_code}."
    return PlatformHealthCheck(
        key=component,
        label=label,
        status=status,
        summary=summary,
    )


def _notification_lag_check(*, now) -> PlatformHealthCheck:
    due = ScheduledNotification.objects.filter(
        status=ScheduledNotification.Status.QUEUED,
        due_at__lte=now,
    ).aggregate(total=Count("id"), oldest_due_at=Min("due_at"))
    total = due["total"] or 0
    oldest_due_at = due["oldest_due_at"]
    if not total or oldest_due_at is None:
        return PlatformHealthCheck(
            key="notification_lag",
            label="Due notifications",
            status=WorkerHeartbeat.Status.OK,
            summary="No overdue queued notifications.",
        )

    lag = now - oldest_due_at
    status = _threshold_status(
        age=lag,
        warning_after=NOTIFICATION_LAG_WARNING_AFTER,
        critical_after=NOTIFICATION_LAG_CRITICAL_AFTER,
    )
    return PlatformHealthCheck(
        key="notification_lag",
        label="Due notifications",
        status=status,
        summary=f"{total} queued; oldest is {_duration_label(lag)} late.",
    )


def _broadcast_progress_check(
    *,
    now,
) -> tuple[PlatformHealthCheck, tuple[ActiveBroadcastHealth, ...]]:
    campaigns = BroadcastCampaign.objects.filter(
        status__in=[BroadcastCampaign.Status.QUEUED, BroadcastCampaign.Status.SENDING],
    ).select_related("partner").order_by("updated_at", "created_at")
    rows: list[ActiveBroadcastHealth] = []
    for campaign in campaigns:
        age = max(now - campaign.updated_at, timedelta(0))
        health_status = _threshold_status(
            age=age,
            warning_after=BROADCAST_STALL_WARNING_AFTER,
            critical_after=BROADCAST_STALL_CRITICAL_AFTER,
        )
        processed_count = campaign.sent_count + campaign.failed_count + campaign.skipped_count
        total_count = processed_count + campaign.queued_count
        rows.append(
            ActiveBroadcastHealth(
                campaign_id=campaign.id,
                partner_name=campaign.partner.name,
                status=campaign.status,
                health_status=health_status,
                processed_count=processed_count,
                total_count=total_count,
                progress_percent=(
                    round(processed_count / total_count * 100) if total_count else 0
                ),
                updated_at=campaign.updated_at,
                progress_age=_duration_label(age),
            )
        )

    active_broadcasts = tuple(rows)
    if not active_broadcasts:
        return (
            PlatformHealthCheck(
                key="broadcast_progress",
                label="Broadcast progress",
                status=WorkerHeartbeat.Status.OK,
                summary="No active broadcasts.",
            ),
            active_broadcasts,
        )

    status = _worst_status(row.health_status for row in active_broadcasts)
    stalled = sum(row.health_status != WorkerHeartbeat.Status.OK for row in active_broadcasts)
    summary = f"{len(active_broadcasts)} active campaign(s)."
    if stalled:
        summary = f"{len(active_broadcasts)} active; {stalled} without recent progress."
    return (
        PlatformHealthCheck(
            key="broadcast_progress",
            label="Broadcast progress",
            status=status,
            summary=summary,
        ),
        active_broadcasts,
    )


def _diagnostic_problem_check(
) -> tuple[PlatformHealthCheck, tuple[PlatformDiagnostic, ...]]:
    problems = active_platform_problems()
    severity_counts = {
        row["severity"]: row["total"]
        for row in problems.values("severity").annotate(total=Count("id"))
    }
    recent_problems = tuple(problems.order_by("-created_at")[:10])
    critical_count = severity_counts.get(PlatformDiagnostic.Severity.CRITICAL, 0)
    error_count = severity_counts.get(PlatformDiagnostic.Severity.ERROR, 0)
    warning_count = severity_counts.get(PlatformDiagnostic.Severity.WARNING, 0)
    if critical_count or error_count:
        status = WorkerHeartbeat.Status.CRITICAL
    elif warning_count:
        status = WorkerHeartbeat.Status.WARNING
    else:
        status = WorkerHeartbeat.Status.OK
    total = critical_count + error_count + warning_count
    summary = (
        "No unresolved warning, error, or critical diagnostics."
        if not total
        else (
            f"{total} unresolved: {critical_count} critical, "
            f"{error_count} error, {warning_count} warning."
        )
    )
    return (
        PlatformHealthCheck(
            key="diagnostics",
            label="Platform diagnostics",
            status=status,
            summary=summary,
        ),
        recent_problems,
    )


def _threshold_status(*, age, warning_after, critical_after) -> str:
    if age >= critical_after:
        return WorkerHeartbeat.Status.CRITICAL
    if age >= warning_after:
        return WorkerHeartbeat.Status.WARNING
    return WorkerHeartbeat.Status.OK


def _worst_status(statuses) -> str:
    return max(statuses, key=_STATUS_RANK.__getitem__, default=WorkerHeartbeat.Status.OK)


def _age_label(*, now, value) -> str:
    if value is None:
        return "never"
    return f"{_duration_label(max(now - value, timedelta(0)))} ago"


def _duration_label(value: timedelta) -> str:
    seconds = max(0, int(value.total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, remaining_minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {remaining_minutes}m" if remaining_minutes else f"{hours}h"
    days, remaining_hours = divmod(hours, 24)
    return f"{days}d {remaining_hours}h" if remaining_hours else f"{days}d"
