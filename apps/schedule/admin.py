from __future__ import annotations

from django.contrib import admin

from apps.accounts.selectors import partner_ids_for_user
from apps.schedule.models import (
    AvailabilityTemplate,
    AvailabilityTemplateWindow,
    AvailabilityWindow,
    BlockedTime,
    BookingResource,
    PartnerScheduleSettings,
)
from core.admin import PartnerScopedAdminMixin


@admin.register(BookingResource)
class BookingResourceAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = ["name", "partner", "type", "is_default", "is_active", "sort_order"]
    list_filter = ["partner", "type", "is_default", "is_active"]
    search_fields = ["name", "partner__name"]
    readonly_fields = ["advisory_lock_key", "created_at", "updated_at"]
    autocomplete_fields = ["partner"]


@admin.register(PartnerScheduleSettings)
class PartnerScheduleSettingsAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = [
        "partner",
        "confirmation_mode",
        "slot_step_minutes",
        "booking_horizon_days",
        "near_fit_tolerance_minutes",
    ]
    list_filter = ["confirmation_mode", "slot_step_minutes"]
    autocomplete_fields = ["partner"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(AvailabilityWindow)
class AvailabilityWindowAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = ["partner", "booking_resource", "start_at", "end_at", "source_template"]
    list_filter = ["partner", "booking_resource", "source_template"]
    autocomplete_fields = ["partner", "booking_resource", "source_template", "created_by"]
    readonly_fields = ["created_at", "updated_at"]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        is_partner_fk = db_field.name in {"booking_resource", "source_template"}
        if is_partner_fk and not request.user.is_superuser:
            kwargs["queryset"] = db_field.remote_field.model.objects.filter(
                partner_id__in=partner_ids_for_user(request.user)
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(BlockedTime)
class BlockedTimeAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = ["partner", "booking_resource", "start_at", "end_at", "reason"]
    list_filter = ["partner", "booking_resource"]
    autocomplete_fields = ["partner", "booking_resource", "created_by"]
    readonly_fields = ["created_at", "updated_at"]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "booking_resource" and not request.user.is_superuser:
            kwargs["queryset"] = BookingResource.objects.filter(
                partner_id__in=partner_ids_for_user(request.user)
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class AvailabilityTemplateWindowInline(admin.TabularInline):
    model = AvailabilityTemplateWindow
    extra = 0


@admin.register(AvailabilityTemplate)
class AvailabilityTemplateAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = ["name", "partner", "is_active", "sort_order"]
    list_filter = ["partner", "is_active"]
    search_fields = ["name"]
    autocomplete_fields = ["partner"]
    inlines = [AvailabilityTemplateWindowInline]


@admin.register(AvailabilityTemplateWindow)
class AvailabilityTemplateWindowAdmin(admin.ModelAdmin):
    list_display = ["template", "weekday", "start_time", "end_time"]
    list_filter = ["weekday", "template__partner"]
    autocomplete_fields = ["template"]

    def get_queryset(self, request):
        queryset = super().get_queryset(request).select_related("template", "template__partner")
        if request.user.is_superuser:
            return queryset
        return queryset.filter(template__partner_id__in=partner_ids_for_user(request.user))

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "template" and not request.user.is_superuser:
            kwargs["queryset"] = AvailabilityTemplate.objects.filter(
                partner_id__in=partner_ids_for_user(request.user)
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
