from __future__ import annotations

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from apps.accounts.selectors import partner_ids_for_user
from apps.appointments.forms import (
    AppointmentProposalAdminForm,
    AppointmentProposalOptionAdminFormSet,
    AppointmentStatusAdminForm,
    ManualAppointmentAdminForm,
    ManualAppointmentServiceItemFormSet,
    add_domain_errors_to_manual_appointment_form,
    service_queryset_for_actor,
)
from apps.appointments.models import (
    Appointment,
    AppointmentProposal,
    AppointmentProposalOption,
    AppointmentProposalOptionItem,
    AppointmentServiceItem,
)
from apps.appointments.proposals import create_appointment_proposal
from apps.appointments.services import (
    correct_final_appointment_status,
    create_manual_appointment_with_client,
    transition_appointment_status,
)
from apps.clients.models import ClientProfile
from apps.schedule.models import BookingResource
from core.admin import PartnerScopedAdminMixin
from core.timezones import to_partner_time


class AppointmentServiceItemInline(admin.TabularInline):
    model = AppointmentServiceItem
    extra = 0
    readonly_fields = [
        "service",
        "category_name_snapshot",
        "service_name_snapshot",
        "description_snapshot",
        "price_amount",
        "currency",
        "duration_minutes",
        "is_custom",
        "is_overridden",
        "sort_order",
    ]
    can_delete = False


class AppointmentProposalOptionItemInline(admin.TabularInline):
    model = AppointmentProposalOptionItem
    extra = 0
    readonly_fields = [
        "service",
        "category_name_snapshot",
        "service_name_snapshot",
        "description_snapshot",
        "price_amount",
        "currency",
        "duration_minutes",
        "is_custom",
        "is_overridden",
        "sort_order",
    ]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class AppointmentProposalOptionInline(admin.TabularInline):
    model = AppointmentProposalOption
    extra = 0
    fields = [
        "start_at_partner_time",
        "end_at_partner_time",
        "total_duration_minutes",
        "service_total_amount",
        "penalties_total_amount",
        "total_amount",
        "currency",
        "sort_order",
        "selected_at_partner_time",
    ]
    readonly_fields = fields
    can_delete = False

    @admin.display(description="Start at (partner time)")
    def start_at_partner_time(self, obj):
        return _partner_datetime(obj.start_at, obj.proposal.partner.timezone)

    @admin.display(description="End at (partner time)")
    def end_at_partner_time(self, obj):
        return _partner_datetime(obj.end_at, obj.proposal.partner.timezone)

    @admin.display(description="Selected at (partner time)")
    def selected_at_partner_time(self, obj):
        return _partner_datetime(obj.selected_at, obj.proposal.partner.timezone)

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Appointment)
class AppointmentAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    change_form_template = "admin/appointments/appointment/change_form.html"
    list_display = [
        "client_profile",
        "partner",
        "booking_resource",
        "status",
        "start_at",
        "end_at",
        "total_amount",
        "currency",
    ]
    list_filter = ["partner", "booking_resource", "status", "source", "is_near_fit"]
    search_fields = ["client_profile__name", "client_profile__phone"]
    autocomplete_fields = ["partner", "booking_resource", "client_profile", "created_by"]
    readonly_fields = [
        "partner",
        "booking_resource",
        "client_profile",
        "start_at",
        "end_at",
        "status",
        "source",
        "confirmation_mode_snapshot",
        "is_near_fit",
        "client_comment",
        "private_master_note",
        "client_visible_reason",
        "service_total_amount",
        "penalties_total_amount",
        "total_amount",
        "currency",
        "created_by",
        "requested_at",
        "confirmed_at",
        "cancelled_at",
        "completed_at",
        "no_show_at",
        "status_changed_at",
        "last_correction_reason",
        "last_correction_at",
        "last_correction_by",
        "created_at",
        "updated_at",
    ]
    inlines = [AppointmentServiceItemInline]
    actions = ["confirm_requested", "mark_completed", "mark_no_show", "cancel_by_master"]

    def get_urls(self):
        custom_urls = [
            path(
                "<path:object_id>/status/",
                self.admin_site.admin_view(self.status_view),
                name="appointments_appointment_status",
            ),
            path(
                "<path:object_id>/propose-time/",
                self.admin_site.admin_view(self.propose_time_view),
                name="appointments_appointment_propose_time",
            ),
        ]
        return custom_urls + super().get_urls()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser:
            if db_field.name == "booking_resource":
                kwargs["queryset"] = BookingResource.objects.filter(
                    partner_id__in=partner_ids_for_user(request.user)
                )
            elif db_field.name == "client_profile":
                kwargs["queryset"] = ClientProfile.objects.filter(
                    partner_id__in=partner_ids_for_user(request.user)
                )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.action(description="Mark completed")
    def mark_completed(self, request, queryset):
        self._run_status_action(request, queryset, Appointment.Status.COMPLETED)

    @admin.action(description="Mark no-show")
    def mark_no_show(self, request, queryset):
        self._run_status_action(request, queryset, Appointment.Status.NO_SHOW)

    @admin.action(description="Cancel by master")
    def cancel_by_master(self, request, queryset):
        self._run_status_action(request, queryset, Appointment.Status.CANCELLED_BY_MASTER)

    @admin.action(description="Confirm requested appointments")
    def confirm_requested(self, request, queryset):
        self._run_status_action(request, queryset, Appointment.Status.CONFIRMED)

    def has_add_permission(self, request):
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False

    def add_view(self, request, form_url="", extra_context=None):
        """Render the service-backed manual appointment workflow."""

        if not self.has_add_permission(request):
            raise PermissionDenied

        form = ManualAppointmentAdminForm(
            request.POST or None,
            actor=request.user,
        )
        service_formset = ManualAppointmentServiceItemFormSet(
            request.POST or None,
            prefix="service_items",
            form_kwargs={
                "service_queryset": service_queryset_for_actor(request.user),
            },
        )
        if request.method == "POST" and form.is_valid() and service_formset.is_valid():
            try:
                service_items = service_formset.build_service_items(
                    partner=form.cleaned_data["partner"],
                )
                appointment = create_manual_appointment_with_client(
                    actor=request.user,
                    partner=form.cleaned_data["partner"],
                    booking_resource=form.cleaned_data["booking_resource"],
                    client_profile=form.cleaned_data.get("client_profile"),
                    new_client_name=form.cleaned_data["new_client_name"],
                    new_client_phone=form.cleaned_data["new_client_phone"],
                    start_at=form.cleaned_data["start_at"],
                    service_items=service_items,
                    client_comment=form.cleaned_data["client_comment"],
                    private_master_note=form.cleaned_data["private_master_note"],
                )
            except ValidationError as exc:
                add_domain_errors_to_manual_appointment_form(form, exc)
            else:
                self.log_addition(request, appointment, "Created manual appointment.")
                return self.response_add(request, appointment)

        context = {
            **self.admin_site.each_context(request),
            "title": "Create manual appointment",
            "subtitle": None,
            "opts": self.model._meta,
            "app_label": self.model._meta.app_label,
            "form": form,
            "service_formset": service_formset,
            "media": self.media + form.media + service_formset.media,
            "has_view_permission": self.has_view_permission(request),
            "has_add_permission": self.has_add_permission(request),
            "has_change_permission": self.has_change_permission(request),
            "has_delete_permission": False,
            "save_as": False,
            "save_on_top": False,
            **(extra_context or {}),
        }
        return TemplateResponse(
            request,
            "admin/appointments/manual_appointment_add.html",
            context,
        )

    def change_view(self, request, object_id, form_url="", extra_context=None):
        appointment = self.get_object(request, object_id)
        proposal_action_url = None
        if (
            appointment is not None
            and appointment.client_profile.telegram_identity_id
            and appointment.status
            in {
                Appointment.Status.REQUESTED,
                Appointment.Status.CONFIRMED,
            }
        ):
            proposal_action_url = reverse(
                "admin:appointments_appointment_propose_time",
                args=[object_id],
            )
        extra_context = {
            **(extra_context or {}),
            "status_action_url": reverse(
                "admin:appointments_appointment_status",
                args=[object_id],
            ),
            "proposal_action_url": proposal_action_url,
        }
        return super().change_view(
            request,
            object_id,
            form_url=form_url,
            extra_context=extra_context,
        )

    def status_view(self, request, object_id):
        """Run one tenant-scoped appointment status transition or correction."""

        appointment = self.get_object(request, object_id)
        if appointment is None:
            raise Http404("Appointment not found.")
        if not self.has_change_permission(request, appointment):
            raise PermissionDenied

        form = AppointmentStatusAdminForm(
            request.POST or None,
            appointment=appointment,
        )
        if request.method == "POST" and form.is_valid():
            try:
                if appointment.is_final:
                    stored = correct_final_appointment_status(
                        actor=request.user,
                        appointment=appointment,
                        new_status=form.cleaned_data["new_status"],
                        reason=form.cleaned_data["reason"],
                    )
                else:
                    stored = transition_appointment_status(
                        actor=request.user,
                        appointment=appointment,
                        new_status=form.cleaned_data["new_status"],
                        reason=form.cleaned_data["reason"],
                    )
            except ValidationError as exc:
                _add_validation_error(form, exc)
            else:
                self.log_change(
                    request,
                    stored,
                    f"Changed status to {stored.get_status_display()}.",
                )
                self.message_user(
                    request,
                    f"Appointment status changed to {stored.get_status_display()}.",
                )
                return HttpResponseRedirect(
                    reverse(
                        "admin:appointments_appointment_change",
                        args=[stored.id],
                    )
                )

        context = {
            **self.admin_site.each_context(request),
            "title": "Change appointment status",
            "subtitle": str(appointment),
            "opts": self.model._meta,
            "appointment": appointment,
            "form": form,
            "media": self.media + form.media,
            "change_url": reverse(
                "admin:appointments_appointment_change",
                args=[appointment.id],
            ),
        }
        return TemplateResponse(
            request,
            "admin/appointments/appointment/status_form.html",
            context,
        )

    def propose_time_view(self, request, object_id):
        """Create a typed proposal through the appointment owner service."""

        appointment = self.get_object(request, object_id)
        if appointment is None:
            raise Http404("Appointment not found.")
        if not self.has_change_permission(request, appointment):
            raise PermissionDenied
        if appointment.status not in {
            Appointment.Status.REQUESTED,
            Appointment.Status.CONFIRMED,
        }:
            self.message_user(
                request,
                "Only requested or confirmed appointments can receive a time proposal.",
                level=messages.ERROR,
            )
            return HttpResponseRedirect(
                reverse("admin:appointments_appointment_change", args=[appointment.id])
            )
        if not appointment.client_profile.telegram_identity_id:
            self.message_user(
                request,
                "This client has no Telegram connection and cannot respond to a proposal.",
                level=messages.ERROR,
            )
            return HttpResponseRedirect(
                reverse("admin:appointments_appointment_change", args=[appointment.id])
            )

        form = AppointmentProposalAdminForm(request.POST or None)
        option_formset = AppointmentProposalOptionAdminFormSet(
            request.POST or None,
            prefix="options",
            form_kwargs={"appointment": appointment},
        )
        if request.method == "POST" and form.is_valid() and option_formset.is_valid():
            proposal_type = (
                AppointmentProposal.Type.REQUESTED_ALTERNATIVES
                if appointment.status == Appointment.Status.REQUESTED
                else AppointmentProposal.Type.RESCHEDULE_CONFIRMED
            )
            try:
                proposal = create_appointment_proposal(
                    actor=request.user,
                    appointment=appointment,
                    proposal_type=proposal_type,
                    options=option_formset.build_options(),
                    message=form.cleaned_data["message"],
                    reason=form.cleaned_data["reason"],
                )
            except ValidationError as exc:
                _add_validation_error(form, exc)
            else:
                self.log_change(
                    request,
                    appointment,
                    f"Created {proposal.get_type_display()} proposal.",
                )
                self.message_user(request, "Time proposal created and queued for delivery.")
                return HttpResponseRedirect(
                    reverse(
                        "admin:appointments_appointmentproposal_change",
                        args=[proposal.id],
                    )
                )

        context = {
            **self.admin_site.each_context(request),
            "title": "Propose another time",
            "subtitle": _appointment_partner_summary(appointment),
            "opts": self.model._meta,
            "appointment": appointment,
            "form": form,
            "option_formset": option_formset,
            "media": self.media + form.media + option_formset.media,
            "change_url": reverse(
                "admin:appointments_appointment_change",
                args=[appointment.id],
            ),
        }
        return TemplateResponse(
            request,
            "admin/appointments/appointment/proposal_form.html",
            context,
        )

    def _run_status_action(self, request, queryset, status: str) -> None:
        changed_count = 0
        failed_count = 0
        for appointment in queryset:
            try:
                transition_appointment_status(
                    actor=request.user,
                    appointment=appointment,
                    new_status=status,
                )
            except ValidationError:
                failed_count += 1
            else:
                changed_count += 1
        if failed_count:
            self.message_user(
                request,
                f"Updated {changed_count} appointment(s), {failed_count} failed validation.",
                level=messages.WARNING,
            )
            return
        self.message_user(request, f"Updated {changed_count} appointment(s).")


@admin.register(AppointmentProposal)
class AppointmentProposalAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = [
        "appointment",
        "partner",
        "type",
        "status",
        "expires_at_partner_time",
        "responded_at_partner_time",
    ]
    list_filter = ["partner", "type", "status"]
    search_fields = ["appointment__client_profile__name", "message", "reason"]
    fields = [
        "partner",
        "appointment",
        "type",
        "status",
        "created_by",
        "expires_at_partner_time",
        "message",
        "reason",
        "responded_at_partner_time",
        "selected_option",
        "created_at_partner_time",
        "updated_at_partner_time",
    ]
    readonly_fields = fields
    inlines = [AppointmentProposalOptionInline]

    @admin.display(description="Expires at (partner time)", ordering="expires_at")
    def expires_at_partner_time(self, obj):
        return _partner_datetime(obj.expires_at, obj.partner.timezone)

    @admin.display(description="Responded at (partner time)", ordering="responded_at")
    def responded_at_partner_time(self, obj):
        return _partner_datetime(obj.responded_at, obj.partner.timezone)

    @admin.display(description="Created at (partner time)", ordering="created_at")
    def created_at_partner_time(self, obj):
        return _partner_datetime(obj.created_at, obj.partner.timezone)

    @admin.display(description="Updated at (partner time)", ordering="updated_at")
    def updated_at_partner_time(self, obj):
        return _partner_datetime(obj.updated_at, obj.partner.timezone)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AppointmentProposalOption)
class AppointmentProposalOptionAdmin(admin.ModelAdmin):
    list_display = [
        "proposal",
        "start_at_partner_time",
        "end_at_partner_time",
        "total_amount",
        "currency",
        "selected_at_partner_time",
    ]
    list_filter = ["proposal__partner", "proposal__type", "proposal__status"]
    search_fields = ["proposal__appointment__client_profile__name"]
    fields = [
        "proposal",
        "start_at_partner_time",
        "end_at_partner_time",
        "total_duration_minutes",
        "service_total_amount",
        "penalties_total_amount",
        "total_amount",
        "currency",
        "sort_order",
        "selected_at_partner_time",
    ]
    readonly_fields = fields
    inlines = [AppointmentProposalOptionItemInline]

    @admin.display(description="Start at (partner time)", ordering="start_at")
    def start_at_partner_time(self, obj):
        return _partner_datetime(obj.start_at, obj.proposal.partner.timezone)

    @admin.display(description="End at (partner time)", ordering="end_at")
    def end_at_partner_time(self, obj):
        return _partner_datetime(obj.end_at, obj.proposal.partner.timezone)

    @admin.display(description="Selected at (partner time)", ordering="selected_at")
    def selected_at_partner_time(self, obj):
        return _partner_datetime(obj.selected_at, obj.proposal.partner.timezone)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset
        return queryset.filter(proposal__partner_id__in=partner_ids_for_user(request.user))

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


def _add_validation_error(form, exc: ValidationError) -> None:
    """Map owner-service validation failures onto the status form."""

    aliases = {"status": "new_status"}
    if hasattr(exc, "message_dict"):
        for field_name, error_messages in exc.message_dict.items():
            target = aliases.get(field_name, field_name)
            if target not in form.fields:
                target = None
            for error_message in error_messages:
                form.add_error(target, error_message)
        return
    for error_message in exc.messages:
        form.add_error(None, error_message)


def _appointment_partner_summary(appointment: Appointment) -> str:
    start_at = _partner_datetime(appointment.start_at, appointment.partner.timezone)
    return f"{appointment.client_profile.display_name} — {start_at} ({appointment.status})"


def _partner_datetime(value, timezone_name: str) -> str:
    if value is None:
        return "-"
    return to_partner_time(value, timezone_name).strftime("%Y-%m-%d %H:%M %Z")
