from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError as DjangoValidationError

from apps.partners.models import Partner
from apps.schedule.models import (
    AvailabilityTemplate,
    AvailabilityTemplateWindow,
    AvailabilityWindow,
    BlockedTime,
    BookingResource,
    PartnerScheduleSettings,
)
from apps.schedule.services import (
    validate_availability_template_application,
    validate_availability_template_input,
    validate_availability_template_window_input,
    validate_availability_window_input,
    validate_blocked_time_input,
    validate_schedule_settings_input,
)


class AvailabilityWindowAdminForm(forms.ModelForm):
    """Validate admin-created windows with the schedule domain rules."""

    class Meta:
        model = AvailabilityWindow
        fields = ("partner", "booking_resource", "start_at", "end_at", "source_template")

    def clean(self):
        cleaned_data = super().clean()
        partner = cleaned_data.get("partner")
        if partner is None and self.instance.pk:
            partner = self.instance.partner
        required_values = (
            partner,
            cleaned_data.get("booking_resource"),
            cleaned_data.get("start_at"),
            cleaned_data.get("end_at"),
        )
        if not all(required_values):
            return cleaned_data
        try:
            validate_availability_window_input(
                partner=partner,
                booking_resource=cleaned_data["booking_resource"],
                start_at=cleaned_data["start_at"],
                end_at=cleaned_data["end_at"],
                source_template=cleaned_data.get("source_template"),
            )
        except DjangoValidationError as exc:
            _add_domain_errors(self, exc)
        return cleaned_data


class PartnerScheduleSettingsAdminForm(forms.ModelForm):
    """Validate all partner-controlled booking settings before service writes."""

    class Meta:
        model = PartnerScheduleSettings
        fields = (
            "partner",
            "confirmation_mode",
            "slot_step_minutes",
            "booking_horizon_days",
            "near_fit_tolerance_minutes",
            "cancellation_deadline_hours",
        )

    def clean(self):
        cleaned_data = super().clean()
        field_names = (
            "confirmation_mode",
            "slot_step_minutes",
            "booking_horizon_days",
            "near_fit_tolerance_minutes",
            "cancellation_deadline_hours",
        )
        if any(cleaned_data.get(field_name) is None for field_name in field_names):
            return cleaned_data
        try:
            validate_schedule_settings_input(
                confirmation_mode=cleaned_data["confirmation_mode"],
                slot_step_minutes=cleaned_data["slot_step_minutes"],
                booking_horizon_days=cleaned_data["booking_horizon_days"],
                near_fit_tolerance_minutes=cleaned_data["near_fit_tolerance_minutes"],
                cancellation_deadline_hours=cleaned_data["cancellation_deadline_hours"],
            )
        except DjangoValidationError as exc:
            _add_domain_errors(self, exc)
        return cleaned_data


class BlockedTimeAdminForm(forms.ModelForm):
    """Validate admin-created blocks with the schedule domain rules."""

    class Meta:
        model = BlockedTime
        fields = ("partner", "booking_resource", "start_at", "end_at", "reason")

    def clean(self):
        cleaned_data = super().clean()
        partner = cleaned_data.get("partner")
        if partner is None and self.instance.pk:
            partner = self.instance.partner
        required_values = (
            partner,
            cleaned_data.get("booking_resource"),
            cleaned_data.get("start_at"),
            cleaned_data.get("end_at"),
        )
        if not all(required_values):
            return cleaned_data
        try:
            validate_blocked_time_input(
                partner=partner,
                booking_resource=cleaned_data["booking_resource"],
                start_at=cleaned_data["start_at"],
                end_at=cleaned_data["end_at"],
            )
        except DjangoValidationError as exc:
            _add_domain_errors(self, exc)
        return cleaned_data


class AvailabilityTemplateAdminForm(forms.ModelForm):
    """Validate reusable schedule template metadata before service writes."""

    class Meta:
        model = AvailabilityTemplate
        fields = ("partner", "name", "is_active", "sort_order")

    def clean(self):
        cleaned_data = super().clean()
        name = cleaned_data.get("name")
        sort_order = cleaned_data.get("sort_order")
        if name is None or sort_order is None:
            return cleaned_data
        try:
            cleaned_data["name"] = validate_availability_template_input(
                name=name,
                sort_order=sort_order,
            )
        except DjangoValidationError as exc:
            _add_domain_errors(self, exc)
        return cleaned_data


class AvailabilityTemplateWindowAdminForm(forms.ModelForm):
    """Validate a weekly template rule before service writes."""

    class Meta:
        model = AvailabilityTemplateWindow
        fields = ("template", "weekday", "start_time", "end_time")

    def clean(self):
        cleaned_data = super().clean()
        template = cleaned_data.get("template")
        if self.instance.pk and template is not None:
            original_partner_id = (
                AvailabilityTemplateWindow.objects.filter(id=self.instance.id)
                .values_list("template__partner_id", flat=True)
                .get()
            )
            if template.partner_id != original_partner_id:
                self.add_error("template", "Template must belong to the same partner.")
        required_values = (
            cleaned_data.get("weekday"),
            cleaned_data.get("start_time"),
            cleaned_data.get("end_time"),
        )
        if any(value is None for value in required_values):
            return cleaned_data
        try:
            validate_availability_template_window_input(
                weekday=cleaned_data["weekday"],
                start_time=cleaned_data["start_time"],
                end_time=cleaned_data["end_time"],
            )
        except DjangoValidationError as exc:
            _add_domain_errors(self, exc)
        return cleaned_data


class ApplyAvailabilityTemplateAdminForm(forms.Form):
    """Collect a booking resource and future date range for template application."""

    booking_resource = forms.ModelChoiceField(queryset=BookingResource.objects.none())
    start_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    end_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(
        self,
        *args,
        partner: Partner,
        template: AvailabilityTemplate,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.partner = partner
        self.template = template
        self.fields["booking_resource"].queryset = BookingResource.objects.filter(
            partner=partner,
            is_active=True,
        ).order_by("sort_order", "name")

    def clean(self):
        cleaned_data = super().clean()
        required_values = (
            cleaned_data.get("booking_resource"),
            cleaned_data.get("start_date"),
            cleaned_data.get("end_date"),
        )
        if not all(required_values):
            return cleaned_data
        try:
            validate_availability_template_application(
                partner=self.partner,
                template=self.template,
                booking_resource=cleaned_data["booking_resource"],
                start_date=cleaned_data["start_date"],
                end_date=cleaned_data["end_date"],
            )
        except DjangoValidationError as exc:
            _add_domain_errors(self, exc)
        return cleaned_data


def _add_domain_errors(form: forms.Form, exc: DjangoValidationError) -> None:
    """Map service validation errors back to form fields when possible."""

    if hasattr(exc, "message_dict"):
        for field_name, messages in exc.message_dict.items():
            target = field_name if field_name in form.fields else None
            for message in messages:
                form.add_error(target, message)
        return
    for message in exc.messages:
        form.add_error(None, message)
