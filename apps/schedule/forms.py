from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError as DjangoValidationError

from apps.schedule.models import AvailabilityWindow
from apps.schedule.services import validate_availability_window_input


class AvailabilityWindowAdminForm(forms.ModelForm):
    """Validate admin-created windows with the schedule domain rules."""

    class Meta:
        model = AvailabilityWindow
        fields = ("partner", "booking_resource", "start_at", "end_at", "source_template")

    def clean(self):
        cleaned_data = super().clean()
        required_values = (
            cleaned_data.get("partner"),
            cleaned_data.get("booking_resource"),
            cleaned_data.get("start_at"),
            cleaned_data.get("end_at"),
        )
        if not all(required_values):
            return cleaned_data
        try:
            validate_availability_window_input(
                partner=cleaned_data["partner"],
                booking_resource=cleaned_data["booking_resource"],
                start_at=cleaned_data["start_at"],
                end_at=cleaned_data["end_at"],
                source_template=cleaned_data.get("source_template"),
            )
        except DjangoValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc
        return cleaned_data
