from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from django import forms
from django.contrib.admin.widgets import AdminSplitDateTime
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import QuerySet
from django.forms import BaseFormSet, formset_factory
from django.utils import timezone

from apps.accounts.selectors import partner_ids_for_user
from apps.appointments.models import Appointment
from apps.appointments.proposals import AppointmentProposalOptionInput
from apps.appointments.services import (
    AppointmentServiceItemInput,
    available_appointment_status_targets,
)
from apps.catalog.models import DEFAULT_CURRENCY, Service
from apps.clients.models import ClientProfile
from apps.partners.models import Partner
from apps.schedule.models import BookingResource
from core.timezones import get_zone


class ManualAppointmentAdminForm(forms.Form):
    """Collect tenant-safe appointment and client data for the admin use case."""

    partner = forms.ModelChoiceField(queryset=Partner.objects.none())
    booking_resource = forms.ModelChoiceField(
        queryset=BookingResource.objects.none(),
        label="Calendar resource",
    )
    start_at = forms.SplitDateTimeField(
        label="Start time",
        widget=AdminSplitDateTime,
    )
    client_profile = forms.ModelChoiceField(
        queryset=ClientProfile.objects.none(),
        required=False,
        label="Existing client",
        help_text="Choose an existing client, or create a manual client below.",
    )
    new_client_name = forms.CharField(
        max_length=255,
        required=False,
        label="New client name",
    )
    new_client_phone = forms.CharField(
        max_length=32,
        required=False,
        label="New client phone",
        help_text="A new manual client needs at least a name or phone.",
    )
    client_comment = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    private_master_note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args, actor, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        visible_partner_ids = partner_ids_for_user(actor)
        if actor.is_superuser:
            partners = Partner.objects.all()
        else:
            partners = Partner.objects.filter(id__in=visible_partner_ids)
        partners = partners.order_by("name")
        self.fields["partner"].queryset = partners

        selected_partner_id = self._selected_partner_id(partners=partners)
        if selected_partner_id is not None:
            selected_partner = partners.get(id=selected_partner_id)
            resources = BookingResource.objects.filter(
                partner_id=selected_partner_id,
                is_active=True,
            )
            clients = ClientProfile.objects.filter(partner_id=selected_partner_id)
            self.fields["start_at"].help_text = f"Enter local time in {selected_partner.timezone}."
        elif actor.is_superuser:
            resources = BookingResource.objects.filter(is_active=True)
            clients = ClientProfile.objects.all()
            self.fields[
                "start_at"
            ].help_text = "The entered time uses the selected partner's timezone."
        else:
            resources = BookingResource.objects.filter(
                partner_id__in=visible_partner_ids,
                is_active=True,
            )
            clients = ClientProfile.objects.filter(partner_id__in=visible_partner_ids)

        self.fields["booking_resource"].queryset = resources.order_by("sort_order", "name")
        self.fields["client_profile"].queryset = clients.select_related(
            "telegram_identity"
        ).order_by("name", "created_at")

    def clean(self):
        cleaned_data = super().clean()
        partner = cleaned_data.get("partner")
        resource = cleaned_data.get("booking_resource")
        client_profile = cleaned_data.get("client_profile")
        new_client_name = (cleaned_data.get("new_client_name") or "").strip()
        new_client_phone = (cleaned_data.get("new_client_phone") or "").strip()
        start_at = cleaned_data.get("start_at")

        if partner is not None and start_at is not None:
            wall_clock = start_at
            if timezone.is_aware(wall_clock):
                wall_clock = timezone.make_naive(
                    wall_clock,
                    timezone.get_current_timezone(),
                )
            cleaned_data["start_at"] = timezone.make_aware(
                wall_clock,
                get_zone(partner.timezone),
            )

        if partner is not None and resource is not None and resource.partner_id != partner.id:
            self.add_error(
                "booking_resource",
                "Calendar resource must belong to the selected partner.",
            )
        if (
            partner is not None
            and client_profile is not None
            and client_profile.partner_id != partner.id
        ):
            self.add_error(
                "client_profile",
                "Client must belong to the selected partner.",
            )

        has_new_client = bool(new_client_name or new_client_phone)
        if client_profile is not None and has_new_client:
            self.add_error(
                "client_profile",
                "Choose an existing client or enter a new client, not both.",
            )
        elif client_profile is None and not has_new_client:
            self.add_error(
                "client_profile",
                "Choose an existing client or enter a new client name or phone.",
            )

        cleaned_data["new_client_name"] = new_client_name
        cleaned_data["new_client_phone"] = new_client_phone
        return cleaned_data

    def _selected_partner_id(self, *, partners):
        raw_partner_id = self.data.get("partner") if self.is_bound else self.initial.get("partner")
        if raw_partner_id:
            try:
                parsed_partner_id = UUID(str(raw_partner_id))
            except (TypeError, ValueError):
                return None
            if partners.filter(id=parsed_partner_id).exists():
                return parsed_partner_id
        if partners.count() == 1:
            partner_id = partners.values_list("id", flat=True).get()
            self.initial.setdefault("partner", partner_id)
            return partner_id
        return None


class AppointmentStatusAdminForm(forms.Form):
    """Collect one allowed status transition or final-status correction."""

    new_status = forms.ChoiceField(label="New status")
    reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Shown to the client for cancellations. Required for final-status corrections.",
    )

    def __init__(self, *args, appointment: Appointment, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.appointment = appointment
        targets = available_appointment_status_targets(appointment=appointment)
        self.fields["new_status"].choices = [("", "Select status")] + [
            (status, Appointment.Status(status).label) for status in targets
        ]
        if appointment.is_final:
            self.fields["reason"].required = True
            self.fields[
                "reason"
            ].help_text = "Required audit reason for correcting a final appointment status."

    def clean_reason(self) -> str:
        reason = self.cleaned_data.get("reason", "").strip()
        if self.appointment.is_final and not reason:
            raise forms.ValidationError("Correction reason is required.")
        return reason


class AppointmentProposalAdminForm(forms.Form):
    """Collect client-visible metadata for a master-created time proposal."""

    message = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Optional message shown to the client with the proposed times.",
    )
    reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=(
            "Optional client-visible reason. For a requested appointment, this is also "
            "used if the unanswered proposal expires and the request is cancelled."
        ),
    )

    def clean_message(self) -> str:
        return self.cleaned_data.get("message", "").strip()

    def clean_reason(self) -> str:
        return self.cleaned_data.get("reason", "").strip()


class AppointmentProposalOptionAdminForm(forms.Form):
    """Collect one alternative start time in the appointment partner's timezone."""

    start_at = forms.SplitDateTimeField(
        label="Proposed start time",
        widget=AdminSplitDateTime,
    )

    def __init__(self, *args, appointment: Appointment, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.appointment = appointment
        self.fields["start_at"].help_text = f"Enter local time in {appointment.partner.timezone}."

    def clean_start_at(self):
        start_at = self.cleaned_data["start_at"]
        wall_clock = start_at
        if timezone.is_aware(wall_clock):
            wall_clock = timezone.make_naive(
                wall_clock,
                timezone.get_current_timezone(),
            )
        localized = timezone.make_aware(
            wall_clock,
            get_zone(self.appointment.partner.timezone),
        )
        if localized <= timezone.now():
            raise forms.ValidationError("Proposed time must be in the future.")
        if localized == self.appointment.start_at:
            raise forms.ValidationError("Choose a time different from the current booking.")
        return localized


class BaseAppointmentProposalOptionFormSet(BaseFormSet):
    """Validate a non-empty, duplicate-free list of proposal options."""

    def clean(self) -> None:
        super().clean()
        if any(self.errors):
            return

        seen: set = set()
        active_forms = 0
        for form in self.forms:
            if self._should_delete_form(form) or not form.cleaned_data:
                continue
            start_at = form.cleaned_data.get("start_at")
            if start_at is None:
                continue
            active_forms += 1
            if start_at in seen:
                form.add_error("start_at", "Each proposed time must be unique.")
            seen.add(start_at)
        if active_forms == 0:
            raise DjangoValidationError("Add at least one proposed time.")

    def build_options(self) -> tuple[AppointmentProposalOptionInput, ...]:
        if not self.is_valid():
            raise RuntimeError("Proposal option formset must be valid before building inputs.")

        return tuple(
            AppointmentProposalOptionInput(
                start_at=form.cleaned_data["start_at"],
                sort_order=index,
            )
            for index, form in enumerate(self.forms)
            if not self._should_delete_form(form)
            and form.cleaned_data
            and form.cleaned_data.get("start_at") is not None
        )


AppointmentProposalOptionAdminFormSet = formset_factory(
    AppointmentProposalOptionAdminForm,
    formset=BaseAppointmentProposalOptionFormSet,
    extra=2,
    can_delete=True,
)


class ManualAppointmentServiceItemForm(forms.Form):
    """One catalog-backed or custom service line for a manual appointment."""

    service = forms.ModelChoiceField(
        queryset=Service.objects.none(),
        required=False,
        label="Catalog service",
    )
    custom_name = forms.CharField(
        max_length=255,
        required=False,
        label="Custom service name",
    )
    description = forms.CharField(
        required=False,
        label="Description override / custom description",
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    price = forms.DecimalField(
        required=False,
        min_value=Decimal("0"),
        max_digits=12,
        decimal_places=2,
        label="Price / override, UAH",
        help_text="Leave blank to use the catalog price.",
    )
    duration_minutes = forms.IntegerField(
        required=False,
        min_value=1,
        label="Duration / override, minutes",
        help_text="Leave blank to use the catalog duration.",
    )

    def __init__(self, *args, service_queryset, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["service"].queryset = service_queryset

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("DELETE"):
            return cleaned_data
        if not self.has_service_line_data(cleaned_data):
            return cleaned_data

        service = cleaned_data.get("service")
        custom_name = (cleaned_data.get("custom_name") or "").strip()
        cleaned_data["custom_name"] = custom_name
        if service is not None:
            if custom_name:
                self.add_error(
                    "custom_name",
                    "Leave custom name blank when a catalog service is selected.",
                )
            return cleaned_data

        if not custom_name:
            self.add_error("custom_name", "Custom service name is required.")
        if cleaned_data.get("price") is None:
            self.add_error("price", "Custom service price is required.")
        if cleaned_data.get("duration_minutes") is None:
            self.add_error(
                "duration_minutes",
                "Custom service duration is required.",
            )
        return cleaned_data

    @staticmethod
    def has_service_line_data(cleaned_data: dict) -> bool:
        return any(
            (
                cleaned_data.get("service") is not None,
                bool((cleaned_data.get("custom_name") or "").strip()),
                bool((cleaned_data.get("description") or "").strip()),
                cleaned_data.get("price") is not None,
                cleaned_data.get("duration_minutes") is not None,
            )
        )


class BaseManualAppointmentServiceItemFormSet(BaseFormSet):
    """Validate and translate dynamic admin service rows into domain inputs."""

    def clean(self) -> None:
        super().clean()
        if any(self.errors):
            return
        if not any(
            form.has_service_line_data(form.cleaned_data)
            for form in self.forms
            if not self._should_delete_form(form)
        ):
            raise DjangoValidationError("Add at least one service line.")

    def build_service_items(
        self,
        *,
        partner: Partner,
    ) -> tuple[AppointmentServiceItemInput, ...]:
        if not self.is_valid():
            raise RuntimeError("Service item formset must be valid before building inputs.")

        items: list[AppointmentServiceItemInput] = []
        for form in self.forms:
            if self._should_delete_form(form):
                continue
            data = form.cleaned_data
            if not form.has_service_line_data(data):
                continue
            service = data.get("service")
            if service is not None and service.partner_id != partner.id:
                raise DjangoValidationError(
                    {"service_items": "Every service must belong to the selected partner."}
                )
            price = data.get("price")
            items.append(
                AppointmentServiceItemInput(
                    service=service,
                    service_name=data.get("custom_name", ""),
                    description=(data.get("description") or "").strip(),
                    price_amount=_minor_units(price) if price is not None else None,
                    currency=service.currency if service is not None else DEFAULT_CURRENCY,
                    duration_minutes=data.get("duration_minutes"),
                    sort_order=len(items),
                )
            )
        return tuple(items)


ManualAppointmentServiceItemFormSet = formset_factory(
    ManualAppointmentServiceItemForm,
    formset=BaseManualAppointmentServiceItemFormSet,
    extra=1,
    can_delete=True,
)


def service_queryset_for_actor(actor) -> QuerySet[Service]:
    """Return active service choices within the actor's trusted tenant scope."""

    queryset = Service.objects.filter(is_active=True, category__is_active=True).select_related(
        "partner",
        "category",
    )
    if not actor.is_superuser:
        queryset = queryset.filter(partner_id__in=partner_ids_for_user(actor))
    return queryset.order_by("partner__name", "category__sort_order", "sort_order", "name")


def add_domain_errors_to_manual_appointment_form(
    form: ManualAppointmentAdminForm,
    exc: DjangoValidationError,
) -> None:
    """Map owner-service validation errors back to useful admin form fields."""

    field_aliases = {
        "client": "client_profile",
        "service_items": None,
        "service": None,
        "service_name": None,
        "price_amount": None,
        "duration_minutes": None,
        "currency": None,
    }
    if hasattr(exc, "message_dict"):
        for field_name, messages in exc.message_dict.items():
            target = field_aliases.get(field_name, field_name)
            if target not in form.fields:
                target = None
            for message in messages:
                form.add_error(target, message)
        return
    for message in exc.messages:
        form.add_error(None, message)


def _minor_units(value: Decimal) -> int:
    return int(value * 100)
