from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.database.models import PartnerBoundModel

DEFAULT_CURRENCY = "UAH"


class ServiceCategory(PartnerBoundModel):
    """Partner-defined grouping for services shown in the booking flow."""

    name = models.CharField(_("name"), max_length=255)
    description = models.TextField(_("description"), blank=True)
    sort_order = models.PositiveIntegerField(_("sort order"), default=0)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("service category")
        verbose_name_plural = _("service categories")
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
        verbose_name=_("category"),
    )
    name = models.CharField(_("name"), max_length=255)
    description = models.TextField(_("description"), blank=True)
    price_amount = models.PositiveIntegerField(_("price amount"), default=0)
    currency = models.CharField(_("currency"), max_length=3, default=DEFAULT_CURRENCY)
    duration_minutes = models.PositiveIntegerField(_("duration in minutes"))
    sort_order = models.PositiveIntegerField(_("sort order"), default=0)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("service")
        verbose_name_plural = _("services")
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
