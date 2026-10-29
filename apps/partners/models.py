from django.db import models

from core.database.models import TimeStampedModel

PARTNER_LANGUAGE_CHOICES = (
    ("uk", "Ukrainian"),
    ("ru", "Russian"),
    ("en", "English"),
)
SUPPORTED_PARTNER_LANGUAGES = frozenset(value for value, _label in PARTNER_LANGUAGE_CHOICES)


class Partner(TimeStampedModel):
    """Platform tenant for one solo master or future service business."""

    class Status(models.TextChoices):
        SETUP_REQUIRED = "setup_required", "Setup required"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        ARCHIVED = "archived", "Archived"

    name = models.CharField(max_length=255)
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.SETUP_REQUIRED,
    )
    timezone = models.CharField(max_length=64, default="Europe/Kiev")
    default_language = models.CharField(max_length=8, default="uk")

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name
