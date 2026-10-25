from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError as DjangoValidationError

from apps.bot.content import TemplateDefinition
from apps.bot.models import BotInstance
from apps.bot.services import validate_bot_token_for_partner, validate_template_body

BOT_WORKSPACE_ACTION_CHECK_CONNECTION = "check_connection"
BOT_WORKSPACE_ACTION_LAUNCH = "launch"
BOT_WORKSPACE_ACTION_PAUSE = "pause"
BOT_WORKSPACE_ACTION_CREATE_MASTER_LINK = "create_master_link"
BOT_WORKSPACE_ACTION_SAVE_CONTENT = "save_content"
BOT_WORKSPACE_ACTION_PREVIEW_CONTENT = "preview_content"
BOT_WORKSPACE_ACTION_RESET_CONTENT = "reset_content"


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
