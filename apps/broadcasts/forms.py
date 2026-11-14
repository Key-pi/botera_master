from __future__ import annotations

from uuid import UUID

from django import forms
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.broadcasts.models import BroadcastCampaign, BroadcastTemplate
from apps.broadcasts.services import (
    MAX_LONG_ABSENT_DAYS,
    validate_broadcast_content,
    validate_broadcast_filters,
)
from apps.catalog.models import Service

AUDIENCE_ALL = "all"
AUDIENCE_LONG_ABSENT = "long_absent"
AUDIENCE_SERVICE = "service"
AUDIENCE_HAS_FUTURE = "has_future"
AUDIENCE_WITHOUT_FUTURE = "without_future"

DELIVERY_DRAFT = "draft"
DELIVERY_NOW = "now"
DELIVERY_SCHEDULED = "scheduled"


class BroadcastContentAdminForm(forms.ModelForm):
    """Shared human-friendly button fields for campaign and template forms."""

    include_book_button = forms.BooleanField(
        required=False,
        label=_("Add Book button"),
    )
    book_button_label = forms.CharField(
        required=False,
        max_length=64,
        initial="Записатися",
    )
    include_my_appointments_button = forms.BooleanField(
        required=False,
        label=_("Add My appointments button"),
    )
    my_appointments_button_label = forms.CharField(
        required=False,
        max_length=64,
        initial="Мої записи",
    )
    external_link_label = forms.CharField(required=False, max_length=64)
    external_link_url = forms.URLField(required=False, assume_scheme="https")

    def _initialize_button_fields(self) -> None:
        if self.is_bound or self.instance._state.adding:
            return
        self.initial.update(button_form_initial(self.instance.buttons))

    def _clean_content(self, cleaned_data: dict) -> None:
        buttons = _buttons_from_cleaned_data(form=self, cleaned_data=cleaned_data)
        if buttons is None:
            return
        try:
            text, photo, buttons = validate_broadcast_content(
                text=cleaned_data.get("text", ""),
                photo=cleaned_data.get("photo", ""),
                buttons=buttons,
            )
        except DjangoValidationError as exc:
            _add_domain_errors(self, exc)
        else:
            cleaned_data.update(text=text, photo=photo, buttons=buttons)


class BroadcastCampaignAdminForm(BroadcastContentAdminForm):
    """Compose one campaign without exposing JSON targeting/button fields."""

    audience = forms.ChoiceField(
        choices=(
            (AUDIENCE_ALL, _("All reachable subscribed clients")),
            (AUDIENCE_LONG_ABSENT, _("Clients absent for a number of days")),
            (AUDIENCE_SERVICE, _("Clients who completed a specific service")),
            (AUDIENCE_HAS_FUTURE, _("Clients with a future appointment")),
            (AUDIENCE_WITHOUT_FUTURE, _("Clients without a future appointment")),
        ),
        initial=AUDIENCE_ALL,
    )
    service = forms.ModelChoiceField(
        queryset=Service.objects.none(),
        required=False,
        help_text=_("Required only for the specific-service audience."),
    )
    long_absent_days = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=MAX_LONG_ABSENT_DAYS,
        help_text=_("Required only for the long-absent audience."),
    )
    delivery_mode = forms.ChoiceField(
        choices=(
            (DELIVERY_DRAFT, _("Save draft")),
            (DELIVERY_NOW, _("Start sending now")),
            (DELIVERY_SCHEDULED, _("Schedule for later")),
        ),
        initial=DELIVERY_DRAFT,
    )

    class Meta:
        model = BroadcastCampaign
        fields = (
            "partner",
            "text",
            "photo",
            "audience",
            "service",
            "long_absent_days",
            "include_book_button",
            "book_button_label",
            "include_my_appointments_button",
            "my_appointments_button_label",
            "external_link_label",
            "external_link_url",
            "delivery_mode",
            "scheduled_at",
        )
        widgets = {
            "text": forms.Textarea(attrs={"rows": 6}),
            "scheduled_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }
        help_texts = {
            "photo": _("Optional Telegram file ID or HTTPS image URL."),
            "scheduled_at": _("Used only when delivery mode is Schedule for later."),
        }

    def __init__(self, *args, allowed_partner_ids=(), **kwargs) -> None:
        self.allowed_partner_ids = tuple(allowed_partner_ids)
        super().__init__(*args, **kwargs)
        self._initialize_button_fields()
        if "service" not in self.fields:
            return
        selected_partner_id = _selected_partner_id(self)
        if selected_partner_id and selected_partner_id in self.allowed_partner_ids:
            self.fields["service"].queryset = Service.objects.filter(
                partner_id=selected_partner_id
            ).order_by("category__sort_order", "sort_order", "name")
        if not self.instance._state.adding and not self.is_bound:
            self.initial.update(_campaign_filter_initial(self.instance.filters))
            self.initial["delivery_mode"] = (
                DELIVERY_SCHEDULED
                if self.instance.status == BroadcastCampaign.Status.SCHEDULED
                else DELIVERY_DRAFT
            )

    def clean(self):
        cleaned_data = super().clean()
        self._clean_content(cleaned_data)
        partner = cleaned_data.get("partner")
        if partner is None and self.instance.pk:
            partner = self.instance.partner
        if partner is None:
            return cleaned_data

        filters = _filters_from_cleaned_data(form=self, cleaned_data=cleaned_data)
        if filters is not None:
            try:
                cleaned_data["filters"] = validate_broadcast_filters(
                    partner=partner,
                    filters=filters,
                )
            except DjangoValidationError as exc:
                _add_domain_errors(
                    self,
                    exc,
                    aliases={"service_id": "service", "filters": "audience"},
                )

        delivery_mode = cleaned_data.get("delivery_mode")
        scheduled_at = cleaned_data.get("scheduled_at")
        if delivery_mode == DELIVERY_SCHEDULED:
            if scheduled_at is None:
                self.add_error("scheduled_at", _("Choose when this broadcast should start."))
            elif scheduled_at <= timezone.now():
                self.add_error("scheduled_at", _("Scheduled time must be in the future."))
        else:
            cleaned_data["scheduled_at"] = None
        cleaned_data["start_immediately"] = delivery_mode == DELIVERY_NOW
        return cleaned_data


class BroadcastTemplateAdminForm(BroadcastContentAdminForm):
    """Edit reusable campaign content without exposing the buttons JSON field."""

    class Meta:
        model = BroadcastTemplate
        fields = (
            "partner",
            "name",
            "text",
            "photo",
            "include_book_button",
            "book_button_label",
            "include_my_appointments_button",
            "my_appointments_button_label",
            "external_link_label",
            "external_link_url",
        )
        widgets = {"text": forms.Textarea(attrs={"rows": 6})}
        help_texts = {"photo": _("Optional Telegram file ID or HTTPS image URL.")}

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._initialize_button_fields()

    def clean(self):
        cleaned_data = super().clean()
        self._clean_content(cleaned_data)
        return cleaned_data


def campaign_form_initial_from_template(template: BroadcastTemplate) -> dict:
    """Return safe add-form initial values for reusing one saved template."""

    return {
        "partner": template.partner_id,
        "text": template.text,
        "photo": template.photo,
        **button_form_initial(template.buttons),
    }


def button_form_initial(buttons: list[dict]) -> dict:
    initial: dict = {}
    for button in buttons:
        button_type = button.get("type")
        if button_type == "book":
            initial["include_book_button"] = True
            initial["book_button_label"] = button.get("label", "")
        elif button_type == "my_appointments":
            initial["include_my_appointments_button"] = True
            initial["my_appointments_button_label"] = button.get("label", "")
        elif button_type == "external_link":
            initial["external_link_label"] = button.get("label", "")
            initial["external_link_url"] = button.get("url", "")
    return initial


def _buttons_from_cleaned_data(*, form, cleaned_data: dict) -> list[dict] | None:
    buttons: list[dict] = []
    if cleaned_data.get("include_book_button"):
        label = cleaned_data.get("book_button_label", "").strip()
        if not label:
            form.add_error("book_button_label", _("Enter a label for the Book button."))
        else:
            buttons.append({"type": "book", "label": label})
    if cleaned_data.get("include_my_appointments_button"):
        label = cleaned_data.get("my_appointments_button_label", "").strip()
        if not label:
            form.add_error(
                "my_appointments_button_label",
                _("Enter a label for the My appointments button."),
            )
        else:
            buttons.append({"type": "my_appointments", "label": label})

    external_label = cleaned_data.get("external_link_label", "").strip()
    external_url = cleaned_data.get("external_link_url", "").strip()
    if external_label or external_url:
        if not external_label:
            form.add_error("external_link_label", _("Enter an external-link label."))
        if not external_url:
            form.add_error("external_link_url", _("Enter an external-link URL."))
        if external_label and external_url:
            buttons.append(
                {
                    "type": "external_link",
                    "label": external_label,
                    "url": external_url,
                }
            )
    if form.errors:
        return None
    return buttons


def _filters_from_cleaned_data(*, form, cleaned_data: dict) -> dict | None:
    audience = cleaned_data.get("audience")
    if audience == AUDIENCE_ALL:
        return {}
    if audience == AUDIENCE_LONG_ABSENT:
        days = cleaned_data.get("long_absent_days")
        if days is None:
            form.add_error("long_absent_days", _("Enter the absence period in days."))
            return None
        return {"long_absent_days": days}
    if audience == AUDIENCE_SERVICE:
        service = cleaned_data.get("service")
        if service is None:
            form.add_error("service", _("Choose a service."))
            return None
        return {"service_id": str(service.id)}
    if audience == AUDIENCE_HAS_FUTURE:
        return {"has_future_appointment": True}
    if audience == AUDIENCE_WITHOUT_FUTURE:
        return {"without_future_appointment": True}
    return None


def _campaign_filter_initial(filters: dict) -> dict:
    if filters.get("service_id"):
        return {"audience": AUDIENCE_SERVICE, "service": filters["service_id"]}
    if filters.get("long_absent_days"):
        return {
            "audience": AUDIENCE_LONG_ABSENT,
            "long_absent_days": filters["long_absent_days"],
        }
    if filters.get("has_future_appointment") is True:
        return {"audience": AUDIENCE_HAS_FUTURE}
    if filters.get("without_future_appointment") is True:
        return {"audience": AUDIENCE_WITHOUT_FUTURE}
    return {"audience": AUDIENCE_ALL}


def _selected_partner_id(form) -> object | None:
    raw_partner_id = form.data.get("partner") if form.is_bound else form.initial.get("partner")
    if not form.instance._state.adding:
        raw_partner_id = form.instance.partner_id
    raw_partner_id = getattr(raw_partner_id, "pk", raw_partner_id)
    try:
        return UUID(str(raw_partner_id))
    except (AttributeError, TypeError, ValueError):
        return None


def _add_domain_errors(
    form: forms.Form,
    exc: DjangoValidationError,
    *,
    aliases: dict[str, str] | None = None,
) -> None:
    aliases = aliases or {}
    if hasattr(exc, "message_dict"):
        for field_name, messages in exc.message_dict.items():
            target = aliases.get(field_name, field_name)
            if target not in form.fields:
                target = None
            for message in messages:
                form.add_error(target, message)
        return
    for message in exc.messages:
        form.add_error(None, message)
