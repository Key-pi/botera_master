from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError as DjangoValidationError

from apps.clients.models import ClientProfile
from apps.clients.services import validate_client_profile_details


class ClientProfileAdminForm(forms.ModelForm):
    """Validate the small partner-editable portion of a client profile."""

    language = forms.ChoiceField(
        choices=(
            ("uk", "Ukrainian"),
            ("ru", "Russian"),
            ("en", "English"),
        )
    )

    class Meta:
        model = ClientProfile
        fields = (
            "name",
            "phone",
            "language",
            "private_master_note",
            "extra_client_text",
        )
        widgets = {
            "private_master_note": forms.Textarea(attrs={"rows": 3}),
            "extra_client_text": forms.Textarea(attrs={"rows": 3}),
        }

    def clean(self):
        cleaned_data = super().clean()
        if not self.instance.pk or "language" not in cleaned_data:
            return cleaned_data
        try:
            details = validate_client_profile_details(
                partner=self.instance.partner,
                has_telegram_identity=self.instance.has_telegram_identity,
                name=cleaned_data.get("name", ""),
                phone=cleaned_data.get("phone", ""),
                language=cleaned_data["language"],
                private_master_note=cleaned_data.get("private_master_note", ""),
                extra_client_text=cleaned_data.get("extra_client_text", ""),
            )
        except DjangoValidationError as exc:
            _add_domain_errors(self, exc)
        else:
            cleaned_data.update(
                name=details.name,
                phone=details.phone,
                language=details.language,
                private_master_note=details.private_master_note,
                extra_client_text=details.extra_client_text,
            )
        return cleaned_data


def _add_domain_errors(form: forms.Form, exc: DjangoValidationError) -> None:
    if hasattr(exc, "message_dict"):
        for field_name, messages in exc.message_dict.items():
            target = field_name if field_name in form.fields else None
            for message in messages:
                form.add_error(target, message)
        return
    for message in exc.messages:
        form.add_error(None, message)
