from __future__ import annotations

from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError as DjangoValidationError

from apps.catalog.models import Service, ServiceCategory
from apps.catalog.services import (
    validate_service_category_input,
    validate_service_input,
)
from core.money import Money


class ServiceCategoryAdminForm(forms.ModelForm):
    """Validate partner-controlled category metadata before service writes."""

    class Meta:
        model = ServiceCategory
        fields = (
            "partner",
            "name",
            "description",
            "sort_order",
            "is_active",
        )
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def clean(self):
        cleaned_data = super().clean()
        if "name" not in cleaned_data or "sort_order" not in cleaned_data:
            return cleaned_data
        try:
            details = validate_service_category_input(
                name=cleaned_data["name"],
                description=cleaned_data.get("description", ""),
                sort_order=cleaned_data["sort_order"],
                is_active=cleaned_data.get("is_active", False),
            )
        except DjangoValidationError as exc:
            _add_domain_errors(self, exc)
        else:
            cleaned_data.update(
                name=details.name,
                description=details.description,
                sort_order=details.sort_order,
                is_active=details.is_active,
            )
        return cleaned_data


class ServiceAdminForm(forms.ModelForm):
    """Expose a major-unit price while persisting integer minor units."""

    price = forms.DecimalField(
        min_value=Decimal("0"),
        max_value=Decimal("21474836.47"),
        max_digits=10,
        decimal_places=2,
        label="Price",
        help_text="Enter the client-facing amount, for example 500.00.",
    )

    class Meta:
        model = Service
        fields = (
            "partner",
            "category",
            "name",
            "description",
            "price",
            "currency",
            "duration_minutes",
            "sort_order",
            "is_active",
        )
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if self.instance.pk and not self.is_bound:
            self.initial["price"] = Money(
                amount=self.instance.price_amount,
                currency=self.instance.currency,
            ).to_major_decimal()

    def clean(self):
        cleaned_data = super().clean()
        partner = cleaned_data.get("partner")
        if partner is None and self.instance.pk:
            partner = self.instance.partner
        required_values = (
            partner,
            cleaned_data.get("category"),
            cleaned_data.get("name"),
            cleaned_data.get("price"),
            cleaned_data.get("currency"),
            cleaned_data.get("duration_minutes"),
            cleaned_data.get("sort_order"),
        )
        if any(value is None for value in required_values):
            return cleaned_data

        price_amount = Money.from_major(
            cleaned_data["price"],
            cleaned_data["currency"],
        ).amount
        try:
            details = validate_service_input(
                partner=partner,
                category=cleaned_data["category"],
                name=cleaned_data["name"],
                description=cleaned_data.get("description", ""),
                price_amount=price_amount,
                currency=cleaned_data["currency"],
                duration_minutes=cleaned_data["duration_minutes"],
                sort_order=cleaned_data["sort_order"],
                is_active=cleaned_data.get("is_active", False),
            )
        except DjangoValidationError as exc:
            _add_domain_errors(self, exc, aliases={"price_amount": "price"})
        else:
            cleaned_data.update(
                name=details.name,
                description=details.description,
                price_amount=details.price_amount,
                currency=details.currency,
                duration_minutes=details.duration_minutes,
                sort_order=details.sort_order,
                is_active=details.is_active,
            )
        return cleaned_data


def _add_domain_errors(
    form: forms.Form,
    exc: DjangoValidationError,
    *,
    aliases: dict[str, str] | None = None,
) -> None:
    aliases = aliases or {}
    if hasattr(exc, "message_dict"):
        for field_name, messages in exc.message_dict.items():
            target = aliases.get(field_name, field_name)
            if target not in form.fields:
                target = None
            for message in messages:
                form.add_error(target, message)
        return
    for message in exc.messages:
        form.add_error(None, message)
