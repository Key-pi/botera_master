from __future__ import annotations

from django import forms

from apps.appointments.models import Appointment
from apps.finance.models import Penalty
from apps.finance.selectors import active_unpaid_penalties_for_client
from core.money import Money


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
        help_text="Only unpaid penalties for this appointment client are available.",
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
            (Action.MARK_PAID, "Mark as paid"),
            (Action.WAIVE, "Waive penalty"),
        ),
    )
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Optional note. It is saved when the penalty is waived.",
    )

    def clean_note(self) -> str:
        return self.cleaned_data.get("note", "").strip()


def add_domain_errors_to_form(form: forms.Form, exc) -> None:
    """Map a service ValidationError onto fields without exposing internals."""

    if hasattr(exc, "message_dict"):
        for field_name, error_messages in exc.message_dict.items():
            target = field_name if field_name in form.fields else None
            for error_message in error_messages:
                form.add_error(target, error_message)
        return
    for error_message in exc.messages:
        form.add_error(None, error_message)
