from django.conf import settings
from django.db import models

from core.database.models import PartnerBoundModel, RetainUntilModel, TimeStampedModel

AUDIT_RETAIN_DAYS = 180
DIAGNOSTIC_RETAIN_DAYS = 90


class AuditEvent(PartnerBoundModel, RetainUntilModel):
    """Business-friendly partner-visible activity log event."""

    event_type = models.CharField(max_length=128, db_index=True)
    target_type = models.CharField(max_length=128, blank=True)
    target_id = models.CharField(max_length=64, blank=True, db_index=True)
    message = models.TextField(blank=True)
    payload_masked = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="audit_events",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["partner", "created_at"]),
            models.Index(fields=["partner", "event_type", "created_at"]),
            models.Index(fields=["target_type", "target_id"]),
            models.Index(fields=["retain_until"]),
        ]

    def __str__(self) -> str:
        return f"{self.partner}:{self.event_type}:{self.created_at}"


class PlatformDiagnostic(TimeStampedModel, RetainUntilModel):
    """Durable masked operational diagnostic record.

    This is intentionally small in Foundation; taxonomy details are finalized
    while implementing concrete bot/runtime/background flows.
    """

    class Severity(models.TextChoices):
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        ERROR = "error", "Error"
        CRITICAL = "critical", "Critical"

    class Component(models.TextChoices):
        WEB = "web", "Web"
        BOT_RUNNER = "bot_runner", "Bot runner"
        TELEGRAM = "telegram", "Telegram"
        CELERY_BEAT = "celery_beat", "Celery beat"
        NOTIFICATIONS_WORKER = "notifications_worker", "Notifications worker"
        BROADCAST_WORKER = "broadcast_worker", "Broadcast worker"
        DEFAULT_WORKER = "default_worker", "Default worker"
        QUEUE = "queue", "Queue"
        POSTGRES = "postgres", "PostgreSQL"
        REDIS = "redis", "Redis"
        CLEANUP = "cleanup", "Cleanup"
        SECURITY = "security", "Security"
        CONTENT = "content", "Content"
        SCHEDULER = "scheduler", "Scheduler"
        HEALTH = "health", "Health"

    partner = models.ForeignKey(
        "partners.Partner",
        on_delete=models.SET_NULL,
        related_name="platform_diagnostics",
        null=True,
        blank=True,
    )
    component = models.CharField(
        max_length=64,
        choices=Component.choices,
        default=Component.WEB,
        db_index=True,
    )
    code = models.CharField(max_length=128, db_index=True)
    severity = models.CharField(max_length=16, choices=Severity.choices)
    message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    queue = models.CharField(max_length=64, blank=True)
    worker = models.CharField(max_length=128, blank=True)
    target_type = models.CharField(max_length=128, blank=True)
    target_id = models.CharField(max_length=64, blank=True, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["partner", "created_at"]),
            models.Index(fields=["component", "created_at"]),
            models.Index(fields=["severity", "created_at"]),
            models.Index(fields=["code", "created_at"]),
            models.Index(fields=["retain_until"]),
        ]

    def __str__(self) -> str:
        return f"{self.component}:{self.severity}:{self.code}"


class WorkerHeartbeat(TimeStampedModel):
    """Current health state for one worker/component, overwritten over time."""

    class Status(models.TextChoices):
        OK = "ok", "OK"
        WARNING = "warning", "Warning"
        CRITICAL = "critical", "Critical"

    component = models.CharField(
        max_length=64,
        choices=PlatformDiagnostic.Component.choices,
        db_index=True,
    )
    queue = models.CharField(max_length=64, blank=True)
    worker = models.CharField(max_length=128, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OK)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=128, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["component", "queue", "worker"]
        constraints = [
            models.UniqueConstraint(
                fields=["component", "queue", "worker"],
                name="audit_unique_worker_heartbeat",
            ),
        ]
        indexes = [
            models.Index(fields=["component", "status"]),
            models.Index(fields=["status", "last_seen_at"]),
        ]

    def __str__(self) -> str:
        identity = ":".join(part for part in [self.component, self.queue, self.worker] if part)
        return f"{identity} ({self.status})"
