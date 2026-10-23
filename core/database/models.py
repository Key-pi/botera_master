import uuid

from django.db import models


class UUIDPrimaryKeyModel(models.Model):
    """Abstract base model with UUID primary key for domain and platform records."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimeStampedModel(UUIDPrimaryKeyModel):
    """Abstract base model that tracks creation and update timestamps."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class RetainUntilModel(models.Model):
    """Abstract mixin for cleanup-eligible operational records."""

    retain_until = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        abstract = True


class PartnerBoundModel(TimeStampedModel):
    """Abstract base model for records that must belong to one partner."""

    partner = models.ForeignKey(
        "partners.Partner",
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)ss",
    )

    class Meta:
        abstract = True
