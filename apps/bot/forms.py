from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError as DjangoValidationError

from apps.bot.models import BotInstance
from apps.bot.services import validate_bot_token_for_partner


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
