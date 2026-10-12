from django.db import models

from core.database.models import RetainUntilModel, TimeStampedModel


class PlatformDiagnostic(TimeStampedModel, RetainUntilModel):
    """Durable masked operational diagnostic record.

    This is intentionally small in Foundation; taxonomy details are finalized
    while implementing concrete bot/runtime/background flows.
    """

    class Severity(models.TextChoices):
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        CRITICAL = "critical", "Critical"

    code = models.CharField(max_length=128, db_index=True)
    severity = models.CharField(max_length=16, choices=Severity.choices)
    message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["severity", "created_at"]),
            models.Index(fields=["code", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.severity}:{self.code}"
