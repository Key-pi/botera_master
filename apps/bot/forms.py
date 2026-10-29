from __future__ import annotations

from decimal import Decimal
from zoneinfo import available_timezones

from django import forms
from django.core.exceptions import ValidationError as DjangoValidationError

from apps.bot.content import TemplateDefinition
from apps.bot.models import BotInstance
from apps.bot.services import validate_bot_token_for_partner, validate_template_body
from apps.notifications.models import (
    MAX_CLIENT_REMINDER_OFFSET_MINUTES,
    MAX_CLIENT_REMINDER_RULES,
    MAX_MASTER_REMINDER_OFFSET_MINUTES,
    MAX_STALE_REQUESTED_INTERVAL_MINUTES,
    MIN_CLIENT_REMINDER_OFFSET_MINUTES,
    MIN_MASTER_REMINDER_OFFSET_MINUTES,
    MIN_STALE_REQUESTED_INTERVAL_MINUTES,
)
from apps.partners.models import PARTNER_LANGUAGE_CHOICES
from apps.schedule.models import (
    ALLOWED_SLOT_STEPS,
    MAX_BOOKING_HORIZON_DAYS,
    MAX_CANCELLATION_DEADLINE_HOURS,
    MAX_NEAR_FIT_TOLERANCE_MINUTES,
    PartnerScheduleSettings,
)
from apps.schedule.services import validate_schedule_settings_input
from core.money import MAX_MAJOR_AMOUNT, Money

BOT_WORKSPACE_ACTION_CHECK_CONNECTION = "check_connection"
BOT_WORKSPACE_ACTION_LAUNCH = "launch"
BOT_WORKSPACE_ACTION_PAUSE = "pause"
BOT_WORKSPACE_ACTION_CREATE_MASTER_LINK = "create_master_link"
BOT_WORKSPACE_ACTION_SAVE_CONTENT = "save_content"
BOT_WORKSPACE_ACTION_PREVIEW_CONTENT = "preview_content"
BOT_WORKSPACE_ACTION_RESET_CONTENT = "reset_content"
BOT_WORKSPACE_ACTION_SAVE_NOTIFICATION_SETTINGS = "save_notification_settings"
BOT_WORKSPACE_ACTION_SAVE_REMINDER_RULES = "save_reminder_rules"
BOT_WORKSPACE_ACTION_SAVE_BOOKING_SETTINGS = "save_booking_settings"
BOT_WORKSPACE_ACTION_SAVE_PENALTY_SETTINGS = "save_penalty_settings"
BOT_WORKSPACE_ACTION_SAVE_PARTNER_SETTINGS = "save_partner_settings"

PARTNER_TIMEZONE_CHOICES = tuple(
    (timezone_name, timezone_name) for timezone_name in sorted(available_timezones())
)

DURATION_UNIT_CHOICES = (
    ("minutes", "Minutes"),
    ("hours", "Hours"),
    ("days", "Days"),
)
DURATION_UNIT_MULTIPLIERS = {
    "minutes": 1,
    "hours": 60,
    "days": 24 * 60,
}


class BotInstanceAdminForm(forms.ModelForm):
    """Admin adapter form that accepts a token without ever rendering it back."""

    new_token = forms.CharField(
        label="New bot token",
        required=False,
        strip=True,
        widget=forms.PasswordInput(
            render_value=False,
            attrs={"autocomplete": "new-password"},
        ),
        help_text=(
            "Enter a token from BotFather. The saved token is encrypted and cannot be shown "
            "again. Leave blank to keep the current token."
        ),
    )

    class Meta:
        model = BotInstance
        fields = ("partner",)

    def clean_new_token(self) -> str:
        raw_token = self.cleaned_data.get("new_token", "")
        if not raw_token:
            if not self.instance.pk or not self.instance.has_token:
                raise forms.ValidationError("A Telegram bot token is required.")
            return ""

        partner = self.cleaned_data.get("partner")
        if partner is None and self.instance.pk:
            partner = self.instance.partner
        if partner is None:
            return raw_token

        try:
            return validate_bot_token_for_partner(partner=partner, raw_token=raw_token)
        except DjangoValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc


class BotTokenWorkspaceForm(forms.Form):
    """Accept a replacement token without ever rendering the stored value."""

    new_token = forms.CharField(
        label="New bot token",
        strip=True,
        widget=forms.PasswordInput(
            render_value=False,
            attrs={"autocomplete": "new-password"},
        ),
        help_text="The saved token remains encrypted and is never shown again.",
    )

    def __init__(self, *args, partner, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.partner = partner

    def clean_new_token(self) -> str:
        raw_token = self.cleaned_data["new_token"]
        try:
            return validate_bot_token_for_partner(
                partner=self.partner,
                raw_token=raw_token,
            )
        except DjangoValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc


class BotWorkspaceActionForm(forms.Form):
    """Constrain operational workspace actions to a small explicit allowlist."""

    action = forms.ChoiceField(
        widget=forms.HiddenInput,
        choices=(
            (BOT_WORKSPACE_ACTION_CHECK_CONNECTION, "Check connection"),
            (BOT_WORKSPACE_ACTION_LAUNCH, "Launch bot"),
            (BOT_WORKSPACE_ACTION_PAUSE, "Pause bot"),
            (BOT_WORKSPACE_ACTION_CREATE_MASTER_LINK, "Connect master Telegram"),
        ),
    )


class BotPartnerSettingsWorkspaceForm(forms.Form):
    """Validate partner-controlled language and timezone defaults."""

    default_language = forms.ChoiceField(
        label="Default bot language",
        choices=PARTNER_LANGUAGE_CHOICES,
    )
    timezone = forms.ChoiceField(
        label="Timezone",
        choices=PARTNER_TIMEZONE_CHOICES,
    )


class BotContentWorkspaceActionForm(forms.Form):
    """Constrain content editor commands to its explicit workspace actions."""

    action = forms.ChoiceField(
        widget=forms.HiddenInput,
        choices=(
            (BOT_WORKSPACE_ACTION_SAVE_CONTENT, "Save content"),
            (BOT_WORKSPACE_ACTION_PREVIEW_CONTENT, "Preview content"),
            (BOT_WORKSPACE_ACTION_RESET_CONTENT, "Reset content"),
        ),
    )


class BotContentWorkspaceForm(forms.Form):
    """Validate one localized partner override without trusting submitted metadata."""

    body = forms.CharField(
        label="Custom text",
        required=False,
        strip=True,
        widget=forms.Textarea(
            attrs={
                "rows": 10,
                "spellcheck": "true",
                "placeholder": "Leave empty to use the system default.",
            }
        ),
        help_text="Leave empty and save to restore the system default for this language.",
    )

    def __init__(self, *args, definition: TemplateDefinition, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.definition = definition
        if definition.content_type == "button":
            self.fields["body"].widget.attrs["maxlength"] = 64

    def clean_body(self) -> str:
        body = self.cleaned_data["body"].strip()
        if not body:
            return ""
        try:
            validate_template_body(
                body=body,
                allowed_placeholders=self.definition.placeholders,
                sample_context=self.definition.sample_context,
                content_type=self.definition.content_type,
            )
        except DjangoValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc
        return body


class BotNotificationSettingsWorkspaceForm(forms.Form):
    """Validate partner-controlled notification timing settings."""

    quiet_hours_enabled = forms.BooleanField(
        label="Quiet hours enabled",
        required=False,
    )
    quiet_hours_start_local = forms.TimeField(
        label="Quiet hours start",
        input_formats=("%H:%M",),
        widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"}),
    )
    quiet_hours_end_local = forms.TimeField(
        label="Quiet hours end",
        input_formats=("%H:%M",),
        widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"}),
    )
    morning_digest_enabled = forms.BooleanField(
        label="Morning digest enabled",
        required=False,
    )
    morning_digest_time_local = forms.TimeField(
        label="Morning digest time",
        input_formats=("%H:%M",),
        widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"}),
    )
    stale_requested_base_interval_minutes = forms.IntegerField(
        label="First pending-request reminder after",
        min_value=MIN_STALE_REQUESTED_INTERVAL_MINUTES,
        max_value=MAX_STALE_REQUESTED_INTERVAL_MINUTES,
        widget=forms.NumberInput(
            attrs={
                "min": MIN_STALE_REQUESTED_INTERVAL_MINUTES,
                "max": MAX_STALE_REQUESTED_INTERVAL_MINUTES,
                "step": 5,
            }
        ),
        help_text="Minutes",
    )


class BotReminderRulesWorkspaceForm(forms.Form):
    """Collect up to five client reminder offsets and one optional master rule."""

    master_enabled = forms.BooleanField(
        label="Master reminder enabled",
        required=False,
    )
    master_value = forms.IntegerField(
        label="Master reminder",
        min_value=1,
        required=False,
        widget=forms.NumberInput(attrs={"min": 1, "aria-label": "Master reminder amount"}),
    )
    master_unit = forms.ChoiceField(
        label="Master reminder unit",
        choices=DURATION_UNIT_CHOICES,
        initial="minutes",
        widget=forms.Select(attrs={"aria-label": "Master reminder unit"}),
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        for index in range(1, MAX_CLIENT_REMINDER_RULES + 1):
            self.fields[f"client_{index}_value"] = forms.IntegerField(
                label=f"Client reminder {index}",
                min_value=1,
                required=False,
                widget=forms.NumberInput(
                    attrs={
                        "min": 1,
                        "aria-label": f"Client reminder {index} amount",
                    }
                ),
            )
            self.fields[f"client_{index}_unit"] = forms.ChoiceField(
                label=f"Client reminder {index} unit",
                choices=DURATION_UNIT_CHOICES,
                initial="minutes",
                widget=forms.Select(attrs={"aria-label": f"Client reminder {index} unit"}),
            )

    @property
    def client_rows(self) -> tuple[tuple[int, forms.BoundField, forms.BoundField], ...]:
        return tuple(
            (
                index,
                self[f"client_{index}_value"],
                self[f"client_{index}_unit"],
            )
            for index in range(1, MAX_CLIENT_REMINDER_RULES + 1)
        )

    def clean(self) -> dict:
        cleaned_data = super().clean()
        client_offsets: list[int] = []
        for index in range(1, MAX_CLIENT_REMINDER_RULES + 1):
            value = cleaned_data.get(f"client_{index}_value")
            if value is None:
                continue
            unit = cleaned_data.get(f"client_{index}_unit")
            if unit is None:
                continue
            offset = value * DURATION_UNIT_MULTIPLIERS[unit]
            if (
                not MIN_CLIENT_REMINDER_OFFSET_MINUTES
                <= offset
                <= (MAX_CLIENT_REMINDER_OFFSET_MINUTES)
            ):
                self.add_error(
                    f"client_{index}_value",
                    "Client reminders must be between 15 minutes and 30 days.",
                )
                continue
            client_offsets.append(offset)

        if len(set(client_offsets)) != len(client_offsets):
            raise forms.ValidationError("Client reminder times must be unique.")

        master_offset = None
        if cleaned_data.get("master_enabled"):
            master_value = cleaned_data.get("master_value")
            master_unit = cleaned_data.get("master_unit")
            if master_value is None:
                self.add_error("master_value", "Enter a master reminder time.")
            elif master_unit is not None:
                master_offset = master_value * DURATION_UNIT_MULTIPLIERS[master_unit]
                if (
                    not MIN_MASTER_REMINDER_OFFSET_MINUTES
                    <= master_offset
                    <= (MAX_MASTER_REMINDER_OFFSET_MINUTES)
                ):
                    self.add_error(
                        "master_value",
                        "Master reminder must be between 5 minutes and 7 days.",
                    )

        cleaned_data["client_offsets_minutes"] = tuple(sorted(client_offsets, reverse=True))
        cleaned_data["master_offset_minutes"] = master_offset
        return cleaned_data


class BotBookingSettingsWorkspaceForm(forms.Form):
    """Validate one complete partner booking-rules update."""

    confirmation_mode = forms.ChoiceField(
        label="Booking confirmation",
        choices=(
            ("", "Select confirmation mode"),
            (PartnerScheduleSettings.ConfirmationMode.AUTO, "Auto-confirm"),
            (PartnerScheduleSettings.ConfirmationMode.MANUAL, "Manual confirmation"),
        ),
    )
    slot_step_minutes = forms.TypedChoiceField(
        label="Slot step",
        choices=tuple((value, f"{value} minutes") for value in ALLOWED_SLOT_STEPS),
        coerce=int,
    )
    booking_horizon_days = forms.IntegerField(
        label="Booking horizon",
        min_value=1,
        max_value=MAX_BOOKING_HORIZON_DAYS,
        widget=forms.NumberInput(attrs={"min": 1, "max": MAX_BOOKING_HORIZON_DAYS}),
        help_text="Days",
    )
    near_fit_tolerance_minutes = forms.IntegerField(
        label="Near-fit tolerance",
        min_value=0,
        max_value=MAX_NEAR_FIT_TOLERANCE_MINUTES,
        widget=forms.NumberInput(attrs={"min": 0, "max": MAX_NEAR_FIT_TOLERANCE_MINUTES}),
        help_text="Minutes",
    )
    cancellation_deadline_hours = forms.IntegerField(
        label="Cancellation deadline",
        min_value=0,
        max_value=MAX_CANCELLATION_DEADLINE_HOURS,
        widget=forms.NumberInput(attrs={"min": 0, "max": MAX_CANCELLATION_DEADLINE_HOURS}),
        help_text="Hours before appointment",
    )
    require_phone_for_booking = forms.BooleanField(
        label="Require phone for booking",
        required=False,
    )

    def clean(self) -> dict:
        cleaned_data = super().clean()
        field_names = (
            "confirmation_mode",
            "slot_step_minutes",
            "booking_horizon_days",
            "near_fit_tolerance_minutes",
            "cancellation_deadline_hours",
            "require_phone_for_booking",
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
                require_phone_for_booking=cleaned_data["require_phone_for_booking"],
            )
        except DjangoValidationError as exc:
            _add_domain_errors(self, exc)
        return cleaned_data


class BotPenaltySettingsWorkspaceForm(forms.Form):
    """Validate one complete partner penalty-rules update."""

    penalties_enabled = forms.BooleanField(
        label="Enable penalties",
        required=False,
    )
    late_cancel_penalty = forms.DecimalField(
        label="Late-cancellation penalty",
        min_value=Decimal("0"),
        max_value=MAX_MAJOR_AMOUNT,
        max_digits=10,
        decimal_places=2,
        help_text="UAH",
    )
    no_show_penalty = forms.DecimalField(
        label="No-show penalty",
        min_value=Decimal("0"),
        max_value=MAX_MAJOR_AMOUNT,
        max_digits=10,
        decimal_places=2,
        help_text="UAH",
    )
    late_arrival_threshold_minutes = forms.IntegerField(
        label="Late-arrival threshold",
        min_value=1,
        widget=forms.NumberInput(attrs={"min": 1}),
        help_text="Minutes",
    )
    late_arrival_penalty = forms.DecimalField(
        label="Late-arrival penalty",
        min_value=Decimal("0"),
        max_value=MAX_MAJOR_AMOUNT,
        max_digits=10,
        decimal_places=2,
        help_text="UAH",
    )

    def clean(self) -> dict:
        cleaned_data = super().clean()
        money_fields = {
            "late_cancel_penalty": "late_cancel_penalty_amount",
            "no_show_penalty": "no_show_penalty_amount",
            "late_arrival_penalty": "late_arrival_penalty_amount",
        }
        for form_field, service_field in money_fields.items():
            value = cleaned_data.get(form_field)
            if value is not None:
                cleaned_data[service_field] = Money.from_major(value).amount
        return cleaned_data


def _add_domain_errors(form: forms.Form, exc: DjangoValidationError) -> None:
    if hasattr(exc, "message_dict"):
        for field_name, errors in exc.message_dict.items():
            target = field_name if field_name in form.fields else None
            for error in errors:
                form.add_error(target, error)
        return
    for error in exc.messages:
        form.add_error(None, error)
