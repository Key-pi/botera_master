from __future__ import annotations

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.template.response import TemplateResponse

from apps.accounts.selectors import partner_ids_for_user
from apps.appointments.forms import (
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
from apps.appointments.services import (
    create_manual_appointment_with_client,
    transition_appointment_status,
)
from apps.clients.models import ClientProfile
from apps.schedule.models import BookingResource
from core.admin import PartnerScopedAdminMixin


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


class AppointmentProposalOptionInline(admin.TabularInline):
    model = AppointmentProposalOption
    extra = 0
    readonly_fields = [
        "start_at",
        "end_at",
        "total_duration_minutes",
        "service_total_amount",
        "penalties_total_amount",
        "total_amount",
        "currency",
        "sort_order",
        "selected_at",
    ]
    can_delete = False


@admin.register(Appointment)
class AppointmentAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
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
        "expires_at",
        "responded_at",
    ]
    list_filter = ["partner", "type", "status"]
    search_fields = ["appointment__client_profile__name", "message", "reason"]
    autocomplete_fields = ["partner", "appointment", "created_by", "selected_option"]
    readonly_fields = ["created_at", "updated_at"]
    inlines = [AppointmentProposalOptionInline]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser:
            if db_field.name == "appointment":
                kwargs["queryset"] = Appointment.objects.filter(
                    partner_id__in=partner_ids_for_user(request.user)
                )
            elif db_field.name == "selected_option":
                kwargs["queryset"] = AppointmentProposalOption.objects.filter(
                    proposal__partner_id__in=partner_ids_for_user(request.user)
                )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(AppointmentProposalOption)
class AppointmentProposalOptionAdmin(admin.ModelAdmin):
    list_display = [
        "proposal",
        "start_at",
        "end_at",
        "total_amount",
        "currency",
        "selected_at",
    ]
    list_filter = ["proposal__partner", "proposal__type", "proposal__status"]
    search_fields = ["proposal__appointment__client_profile__name"]
    autocomplete_fields = ["proposal"]
    inlines = [AppointmentProposalOptionItemInline]

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset
        return queryset.filter(proposal__partner_id__in=partner_ids_for_user(request.user))

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser and db_field.name == "proposal":
            kwargs["queryset"] = AppointmentProposal.objects.filter(
                partner_id__in=partner_ids_for_user(request.user)
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
