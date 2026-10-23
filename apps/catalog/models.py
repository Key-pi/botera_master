from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from core.database.models import PartnerBoundModel

DEFAULT_CURRENCY = "UAH"


class ServiceCategory(PartnerBoundModel):
    """Partner-defined grouping for services shown in the booking flow."""

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["partner__name", "sort_order", "name"]
        indexes = [
            models.Index(fields=["partner", "is_active", "sort_order"]),
            models.Index(fields=["partner", "sort_order"]),
        ]

    def __str__(self) -> str:
        return self.name


class Service(PartnerBoundModel):
    """Partner-defined service that can be selected for new appointments."""

    category = models.ForeignKey(
        "catalog.ServiceCategory",
        on_delete=models.PROTECT,
        related_name="services",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    price_amount = models.PositiveIntegerField(default=0)
    currency = models.CharField(max_length=3, default=DEFAULT_CURRENCY)
    duration_minutes = models.PositiveIntegerField()
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["partner__name", "category__sort_order", "sort_order", "name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(price_amount__gte=0),
                name="catalog_service_price_amount_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(duration_minutes__gt=0),
                name="catalog_service_duration_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["partner", "is_active", "sort_order"]),
            models.Index(fields=["partner", "category", "sort_order"]),
            models.Index(fields=["category", "is_active", "sort_order"]),
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        if self.category_id and self.partner_id and self.category.partner_id != self.partner_id:
            raise ValidationError({"category": "Service category must belong to the same partner."})
