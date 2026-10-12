from __future__ import annotations

from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from apps.accounts.selectors import partner_ids_for_user
from apps.appointments.models import Appointment, AppointmentServiceItem
from apps.appointments.services import transition_appointment_status
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
    readonly_fields = ["created_at", "updated_at", "status_changed_at"]
    inlines = [AppointmentServiceItemInline]
    actions = ["mark_completed", "mark_no_show", "cancel_by_master"]

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
