from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.database.models import PartnerBoundModel, RetainUntilModel, TimeStampedModel

AUDIT_RETAIN_DAYS = 180
DIAGNOSTIC_RETAIN_DAYS = 90


class AuditEvent(PartnerBoundModel, RetainUntilModel):
    """Business-friendly partner-visible activity log event."""

    event_type = models.CharField(_("event type"), max_length=128, db_index=True)
    target_type = models.CharField(_("target type"), max_length=128, blank=True)
    target_id = models.CharField(_("target ID"), max_length=64, blank=True, db_index=True)
    message = models.TextField(_("message"), blank=True)
    payload_masked = models.JSONField(_("masked payload"), default=dict, blank=True)
    reason = models.TextField(_("reason"), blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="audit_events",
        null=True,
        blank=True,
        verbose_name=_("actor"),
    )

    class Meta:
        verbose_name = _("audit event")
        verbose_name_plural = _("audit events")
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
        INFO = "info", _("Info")
        WARNING = "warning", _("Warning")
        ERROR = "error", _("Error")
        CRITICAL = "critical", _("Critical")

    class Component(models.TextChoices):
        WEB = "web", _("Web")
        BOT_RUNNER = "bot_runner", _("Bot runner")
        TELEGRAM = "telegram", _("Telegram")
        CELERY_BEAT = "celery_beat", _("Celery beat")
        NOTIFICATIONS_WORKER = "notifications_worker", _("Notifications worker")
        BROADCAST_WORKER = "broadcast_worker", _("Broadcast worker")
        DEFAULT_WORKER = "default_worker", _("Default worker")
        QUEUE = "queue", _("Queue")
        POSTGRES = "postgres", _("PostgreSQL")
        REDIS = "redis", _("Redis")
        CLEANUP = "cleanup", _("Cleanup")
        SECURITY = "security", _("Security")
        CONTENT = "content", _("Content")
        SCHEDULER = "scheduler", _("Scheduler")
        HEALTH = "health", _("Health")

    partner = models.ForeignKey(
        "partners.Partner",
        on_delete=models.SET_NULL,
        related_name="platform_diagnostics",
        null=True,
        blank=True,
        verbose_name=_("partner"),
    )
    component = models.CharField(
        _("component"),
        max_length=64,
        choices=Component.choices,
        default=Component.WEB,
        db_index=True,
    )
    code = models.CharField(_("code"), max_length=128, db_index=True)
    severity = models.CharField(_("severity"), max_length=16, choices=Severity.choices)
    message = models.TextField(_("message"), blank=True)
    metadata = models.JSONField(_("metadata"), default=dict, blank=True)
    queue = models.CharField(_("queue"), max_length=64, blank=True)
    worker = models.CharField(_("worker"), max_length=128, blank=True)
    target_type = models.CharField(_("target type"), max_length=128, blank=True)
    target_id = models.CharField(_("target ID"), max_length=64, blank=True, db_index=True)
    resolved_at = models.DateTimeField(_("resolved at"), null=True, blank=True)

    class Meta:
        verbose_name = _("platform diagnostic")
        verbose_name_plural = _("platform diagnostics")
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
        WARNING = "warning", _("Warning")
        CRITICAL = "critical", _("Critical")

    component = models.CharField(
        _("component"),
        max_length=64,
        choices=PlatformDiagnostic.Component.choices,
        db_index=True,
    )
    queue = models.CharField(_("queue"), max_length=64, blank=True)
    worker = models.CharField(_("worker"), max_length=128, blank=True)
    status = models.CharField(_("status"), max_length=16, choices=Status.choices, default=Status.OK)
    last_seen_at = models.DateTimeField(_("last seen at"), null=True, blank=True)
    last_success_at = models.DateTimeField(_("last success at"), null=True, blank=True)
    last_error_at = models.DateTimeField(_("last error at"), null=True, blank=True)
    last_error_code = models.CharField(_("last error code"), max_length=128, blank=True)
    metadata = models.JSONField(_("metadata"), default=dict, blank=True)

    class Meta:
        verbose_name = _("worker heartbeat")
        verbose_name_plural = _("worker heartbeats")
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
