from __future__ import annotations

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

BOT_WORKSPACE_ACTION_CHECK_CONNECTION = "check_connection"
BOT_WORKSPACE_ACTION_LAUNCH = "launch"
BOT_WORKSPACE_ACTION_PAUSE = "pause"
BOT_WORKSPACE_ACTION_CREATE_MASTER_LINK = "create_master_link"
BOT_WORKSPACE_ACTION_SAVE_CONTENT = "save_content"
BOT_WORKSPACE_ACTION_PREVIEW_CONTENT = "preview_content"
BOT_WORKSPACE_ACTION_RESET_CONTENT = "reset_content"
BOT_WORKSPACE_ACTION_SAVE_NOTIFICATION_SETTINGS = "save_notification_settings"
BOT_WORKSPACE_ACTION_SAVE_REMINDER_RULES = "save_reminder_rules"

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
