from django.db import models
from django.utils.translation import gettext_lazy as _

from core.database.models import TimeStampedModel

PARTNER_LANGUAGE_CHOICES = (
    ("uk", _("Ukrainian")),
    ("ru", _("Russian")),
    ("en", _("English")),
)
SUPPORTED_PARTNER_LANGUAGES = frozenset(value for value, _label in PARTNER_LANGUAGE_CHOICES)


class Partner(TimeStampedModel):
    """Platform tenant for one solo master or future service business."""

    class Status(models.TextChoices):
        SETUP_REQUIRED = "setup_required", _("Setup required")
        ACTIVE = "active", _("Active")
        SUSPENDED = "suspended", _("Suspended")
        ARCHIVED = "archived", _("Archived")

    name = models.CharField(_("name"), max_length=255)
    status = models.CharField(
        _("status"),
        max_length=32,
        choices=Status.choices,
        default=Status.SETUP_REQUIRED,
    )
    timezone = models.CharField(_("timezone"), max_length=64, default="Europe/Kyiv")
    default_language = models.CharField(_("default language"), max_length=8, default="uk")

    class Meta:
        verbose_name = _("partner")
        verbose_name_plural = _("partners")
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name
