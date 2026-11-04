from __future__ import annotations

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User
from apps.partners.models import PARTNER_LANGUAGE_CHOICES, Partner
from core.timezones import get_zone


class PartnerOnboardingAdminForm(forms.ModelForm):
    """Superuser form for atomically creating a partner and its owner account."""

    default_language = forms.ChoiceField(choices=PARTNER_LANGUAGE_CHOICES, initial="uk")
    owner_username = forms.CharField(label=_("Owner username"), max_length=150)
    owner_email = forms.EmailField(label=_("Owner email"), required=False)
    owner_first_name = forms.CharField(label=_("Owner first name"), max_length=150, required=False)
    owner_last_name = forms.CharField(label=_("Owner last name"), max_length=150, required=False)
    owner_password1 = forms.CharField(
        label=_("Owner password"),
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
    )
    owner_password2 = forms.CharField(
        label=_("Confirm owner password"),
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
    )

    class Meta:
        model = Partner
        fields = ("name", "timezone", "default_language")

    def clean_timezone(self) -> str:
        timezone_name = self.cleaned_data["timezone"].strip()
        try:
            get_zone(timezone_name)
        except Exception as exc:
            raise forms.ValidationError(_("Unknown timezone.")) from exc
        return timezone_name

    def clean_owner_username(self) -> str:
        username = self.cleaned_data["owner_username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError(_("A user with this username already exists."))
        return username

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("owner_password1")
        password2 = cleaned_data.get("owner_password2")
        if password1 and password2 and password1 != password2:
            self.add_error("owner_password2", _("Passwords do not match."))
            return cleaned_data
        if not password2:
            return cleaned_data

        candidate = User(
            username=cleaned_data.get("owner_username", ""),
            email=cleaned_data.get("owner_email", ""),
            first_name=cleaned_data.get("owner_first_name", ""),
            last_name=cleaned_data.get("owner_last_name", ""),
        )
        try:
            validate_password(password2, user=candidate)
        except DjangoValidationError as exc:
            self.add_error("owner_password2", exc)
        return cleaned_data
