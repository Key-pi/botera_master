from __future__ import annotations

from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import MinValueValidator
from django.utils.translation import gettext_lazy as _

from apps.appointments.models import Appointment
from apps.finance.models import Expense, PartnerPenaltySettings, Penalty
from apps.finance.selectors import active_unpaid_penalties_for_client
from apps.finance.services import validate_expense_input
from core.money import MAX_MAJOR_AMOUNT, Money


class PenaltyChoiceField(forms.ModelMultipleChoiceField):
    """Render a useful business label instead of the raw penalty representation."""

    def label_from_instance(self, penalty: Penalty) -> str:
        amount = Money(amount=penalty.amount, currency=penalty.currency).to_major_decimal()
        return (
            f"{penalty.get_reason_display()} - {amount:.2f} {penalty.currency} "
            f"({penalty.created_at:%Y-%m-%d})"
        )


class AppointmentPenaltyAdminForm(forms.Form):
    """Collect unpaid penalties that the master wants to attach to one booking."""

    penalties = PenaltyChoiceField(
        queryset=Penalty.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        help_text=_("Only unpaid penalties for this appointment client are available."),
    )

    def __init__(self, *args, appointment: Appointment, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["penalties"].queryset = active_unpaid_penalties_for_client(
            appointment.client_profile
        ).filter(currency=appointment.currency)


class ClientPenaltyActionAdminForm(forms.Form):
    """Confirm one explicit debt action from the partner client card."""

    class Action:
        MARK_PAID = "mark_paid"
        WAIVE = "waive"

    action = forms.ChoiceField(
        choices=(
            (Action.MARK_PAID, _("Mark as paid")),
            (Action.WAIVE, _("Waive penalty")),
        ),
    )
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Optional note. It is saved when the penalty is waived."),
    )

    def clean_note(self) -> str:
        return self.cleaned_data.get("note", "").strip()


class NoShowPenaltyAdminForm(forms.Form):
    """Confirm an optional no-show debt from the appointment workflow."""

    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Optional note visible in the partner penalty record."),
    )

    def clean_note(self) -> str:
        return self.cleaned_data.get("note", "").strip()


class LateArrivalPenaltyAdminForm(NoShowPenaltyAdminForm):
    """Collect lateness details before creating the configured penalty."""

    late_by_minutes = forms.IntegerField(
        min_value=1,
        label=_("Late by (minutes)"),
    )

    def __init__(self, *args, threshold_minutes: int, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        field = self.fields["late_by_minutes"]
        field.min_value = threshold_minutes
        field.validators.append(MinValueValidator(threshold_minutes))
        field.widget.attrs["min"] = threshold_minutes
        field.help_text = _("The configured late-arrival threshold is %(count)s minutes.") % {
            "count": threshold_minutes
        }


class PartnerPenaltySettingsAdminForm(forms.ModelForm):
    """Expose penalty defaults in major units while storing integer minor units."""

    late_cancel_penalty = forms.DecimalField(
        min_value=Decimal("0"),
        max_value=MAX_MAJOR_AMOUNT,
        max_digits=10,
        decimal_places=2,
        label=_("Late-cancellation penalty"),
    )
    no_show_penalty = forms.DecimalField(
        min_value=Decimal("0"),
        max_value=MAX_MAJOR_AMOUNT,
        max_digits=10,
        decimal_places=2,
        label=_("No-show penalty"),
    )
    late_arrival_penalty = forms.DecimalField(
        min_value=Decimal("0"),
        max_value=MAX_MAJOR_AMOUNT,
        max_digits=10,
        decimal_places=2,
        label=_("Late-arrival penalty"),
    )

    class Meta:
        model = PartnerPenaltySettings
        fields = (
            "partner",
            "penalties_enabled",
            "late_cancel_penalty",
            "no_show_penalty",
            "late_arrival_threshold_minutes",
            "late_arrival_penalty",
        )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if self.instance.pk and not self.is_bound:
            self.initial.update(
                late_cancel_penalty=_major_amount(self.instance.late_cancel_penalty_amount),
                no_show_penalty=_major_amount(self.instance.no_show_penalty_amount),
                late_arrival_penalty=_major_amount(self.instance.late_arrival_penalty_amount),
            )

    def clean(self):
        cleaned_data = super().clean()
        field_map = {
            "late_cancel_penalty": "late_cancel_penalty_amount",
            "no_show_penalty": "no_show_penalty_amount",
            "late_arrival_penalty": "late_arrival_penalty_amount",
        }
        for form_field, service_field in field_map.items():
            value = cleaned_data.get(form_field)
            if value is not None:
                cleaned_data[service_field] = Money.from_major(value).amount
        return cleaned_data


class ExpenseAdminForm(forms.ModelForm):
    """Collect an expense amount in major units for service-backed writes."""

    amount_major = forms.DecimalField(
        min_value=Decimal("0.01"),
        max_value=MAX_MAJOR_AMOUNT,
        max_digits=10,
        decimal_places=2,
        label=_("Amount"),
    )

    class Meta:
        model = Expense
        fields = (
            "partner",
            "date",
            "amount_major",
            "currency",
            "description",
        )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        if self.instance.pk and not self.is_bound:
            self.initial["amount_major"] = _major_amount(self.instance.amount)

    def clean(self):
        cleaned_data = super().clean()
        required_values = (
            cleaned_data.get("date"),
            cleaned_data.get("amount_major"),
            cleaned_data.get("currency"),
            cleaned_data.get("description"),
        )
        if any(value is None for value in required_values):
            return cleaned_data
        amount = Money.from_major(
            cleaned_data["amount_major"],
            cleaned_data["currency"],
        ).amount
        try:
            details = validate_expense_input(
                date=cleaned_data["date"],
                amount=amount,
                currency=cleaned_data["currency"],
                description=cleaned_data["description"],
            )
        except DjangoValidationError as exc:
            add_domain_errors_to_form(
                self,
                exc,
                aliases={"amount": "amount_major"},
            )
        else:
            cleaned_data.update(
                date=details.date,
                amount=details.amount,
                currency=details.currency,
                description=details.description,
            )
        return cleaned_data


def add_domain_errors_to_form(
    form: forms.Form,
    exc,
    *,
    aliases: dict[str, str] | None = None,
) -> None:
    """Map a service ValidationError onto fields without exposing internals."""

    aliases = aliases or {}
    if hasattr(exc, "message_dict"):
        for field_name, error_messages in exc.message_dict.items():
            target = aliases.get(field_name, field_name)
            if target not in form.fields:
                target = None
            for error_message in error_messages:
                form.add_error(target, error_message)
        return
    for error_message in exc.messages:
        form.add_error(None, error_message)


def _major_amount(amount: int) -> Decimal:
    return Money(amount=amount).to_major_decimal()
