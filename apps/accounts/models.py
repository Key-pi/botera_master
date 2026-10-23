from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models

from core.database.models import TimeStampedModel


class User(AbstractUser):
    """Platform or partner admin account.

    Client-facing Telegram users intentionally live in the future `clients`
    app, not here. This model is only for platform and partner admin access.
    """


class PartnerMembership(TimeStampedModel):
    """Admin access link between a user account and a partner tenant."""

    class Role(models.TextChoices):
        OWNER = "owner", "Owner/master"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="partner_memberships",
    )
    partner = models.ForeignKey(
        "partners.Partner",
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(max_length=32, choices=Role.choices, default=Role.OWNER)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["partner__name", "user__username"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "partner"],
                name="accounts_unique_user_partner_membership",
            )
        ]
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["partner", "role", "is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} -> {self.partner} ({self.role})"
