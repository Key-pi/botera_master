from __future__ import annotations

from datetime import timedelta

from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.core.exceptions import ValidationError
from django.template.response import TemplateResponse
from django.utils import timezone

from apps.accounts.selectors import partner_ids_for_user
from apps.schedule.forms import (
    ApplyAvailabilityTemplateAdminForm,
    AvailabilityTemplateAdminForm,
    AvailabilityTemplateWindowAdminForm,
    AvailabilityWindowAdminForm,
    BlockedTimeAdminForm,
    PartnerScheduleSettingsAdminForm,
)
from apps.schedule.models import (
    AvailabilityTemplate,
    AvailabilityTemplateWindow,
    AvailabilityWindow,
    BlockedTime,
    BookingResource,
    PartnerScheduleSettings,
)
from apps.schedule.services import (
    apply_availability_template,
    create_availability_template,
    create_availability_template_window,
    create_availability_window,
    create_blocked_time,
    delete_availability_template,
    delete_availability_template_window,
    delete_blocked_time,
    update_availability_template,
    update_availability_template_window,
    update_blocked_time,
    update_schedule_settings,
)
from core.admin import PartnerScopedAdminMixin
from core.timezones import get_zone


@admin.register(BookingResource)
class BookingResourceAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = ["name", "partner", "type", "is_default", "is_active", "sort_order"]
    list_filter = ["partner", "type", "is_default", "is_active"]
    search_fields = ["name", "partner__name"]
    readonly_fields = ["advisory_lock_key", "created_at", "updated_at"]
    autocomplete_fields = ["partner"]


@admin.register(PartnerScheduleSettings)
class PartnerScheduleSettingsAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    form = PartnerScheduleSettingsAdminForm
    list_display = [
        "partner",
        "confirmation_mode",
        "slot_step_minutes",
        "booking_horizon_days",
        "near_fit_tolerance_minutes",
    ]
    list_filter = ["confirmation_mode", "slot_step_minutes"]
    readonly_fields = ["partner", "created_at", "updated_at"]

    def save_model(self, request, obj, form, change):
        stored = update_schedule_settings(
            actor=request.user,
            partner=obj.partner,
            confirmation_mode=form.cleaned_data["confirmation_mode"],
            slot_step_minutes=form.cleaned_data["slot_step_minutes"],
            booking_horizon_days=form.cleaned_data["booking_horizon_days"],
            near_fit_tolerance_minutes=form.cleaned_data["near_fit_tolerance_minutes"],
            cancellation_deadline_hours=form.cleaned_data["cancellation_deadline_hours"],
        )
        _copy_model_state(target=obj, source=stored)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AvailabilityWindow)
class AvailabilityWindowAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    form = AvailabilityWindowAdminForm
    list_display = ["partner", "booking_resource", "start_at", "end_at", "source_template"]
    list_filter = ["partner", "booking_resource", "source_template"]
    autocomplete_fields = ["partner", "booking_resource", "source_template"]
    readonly_fields = ["created_by", "created_at", "updated_at"]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        is_partner_fk = db_field.name in {"booking_resource", "source_template"}
        if is_partner_fk and not request.user.is_superuser:
            kwargs["queryset"] = db_field.remote_field.model.objects.filter(
                partner_id__in=partner_ids_for_user(request.user)
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if change:
            super().save_model(request, obj, form, change)
            return

        stored = create_availability_window(
            actor=request.user,
            partner=form.cleaned_data["partner"],
            booking_resource=form.cleaned_data["booking_resource"],
            start_at=form.cleaned_data["start_at"],
            end_at=form.cleaned_data["end_at"],
            source_template=form.cleaned_data.get("source_template"),
        )
        _copy_model_state(target=obj, source=stored)


@admin.register(BlockedTime)
class BlockedTimeAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    form = BlockedTimeAdminForm
    list_display = ["partner", "booking_resource", "start_at", "end_at", "reason"]
    list_filter = ["partner", "booking_resource"]
    autocomplete_fields = ["partner", "booking_resource"]
    readonly_fields = ["created_by", "created_at", "updated_at"]

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            readonly_fields.append("partner")
        return readonly_fields

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "booking_resource" and not request.user.is_superuser:
            kwargs["queryset"] = BookingResource.objects.filter(
                partner_id__in=partner_ids_for_user(request.user)
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if change:
            stored = update_blocked_time(
                actor=request.user,
                blocked_time=obj,
                booking_resource=form.cleaned_data["booking_resource"],
                start_at=form.cleaned_data["start_at"],
                end_at=form.cleaned_data["end_at"],
                reason=form.cleaned_data["reason"],
            )
        else:
            stored = create_blocked_time(
                actor=request.user,
                partner=form.cleaned_data["partner"],
                booking_resource=form.cleaned_data["booking_resource"],
                start_at=form.cleaned_data["start_at"],
                end_at=form.cleaned_data["end_at"],
                reason=form.cleaned_data["reason"],
            )
        _copy_model_state(target=obj, source=stored)

    def delete_model(self, request, obj):
        delete_blocked_time(actor=request.user, blocked_time=obj)

    def delete_queryset(self, request, queryset):
        for blocked_time in queryset.select_related("partner"):
            delete_blocked_time(actor=request.user, blocked_time=blocked_time)


class AvailabilityTemplateWindowInline(admin.TabularInline):
    model = AvailabilityTemplateWindow
    extra = 0
    fields = ["weekday", "start_time", "end_time"]
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(AvailabilityTemplate)
class AvailabilityTemplateAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    form = AvailabilityTemplateAdminForm
    list_display = ["name", "partner", "is_active", "sort_order"]
    list_filter = ["partner", "is_active"]
    search_fields = ["name"]
    autocomplete_fields = ["partner"]
    readonly_fields = ["created_at", "updated_at"]
    inlines = [AvailabilityTemplateWindowInline]
    actions = ["apply_selected_template"]

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            readonly_fields.append("partner")
        return readonly_fields

    def save_model(self, request, obj, form, change):
        if change:
            stored = update_availability_template(
                actor=request.user,
                template=obj,
                name=form.cleaned_data["name"],
                is_active=form.cleaned_data["is_active"],
                sort_order=form.cleaned_data["sort_order"],
            )
        else:
            stored = create_availability_template(
                actor=request.user,
                partner=form.cleaned_data["partner"],
                name=form.cleaned_data["name"],
                is_active=form.cleaned_data["is_active"],
                sort_order=form.cleaned_data["sort_order"],
            )
        _copy_model_state(target=obj, source=stored)

    def delete_model(self, request, obj):
        delete_availability_template(actor=request.user, template=obj)

    def delete_queryset(self, request, queryset):
        for template in queryset.select_related("partner"):
            delete_availability_template(actor=request.user, template=template)

    @admin.action(description="Apply selected template to a date range")
    def apply_selected_template(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(
                request,
                "Select exactly one availability template to apply.",
                level=messages.WARNING,
            )
            return None

        template = queryset.select_related("partner").get()
        if request.POST.get("confirm_apply"):
            form = ApplyAvailabilityTemplateAdminForm(
                request.POST,
                partner=template.partner,
                template=template,
            )
            if form.is_valid():
                try:
                    windows = apply_availability_template(
                        actor=request.user,
                        partner=template.partner,
                        template=template,
                        booking_resource=form.cleaned_data["booking_resource"],
                        start_date=form.cleaned_data["start_date"],
                        end_date=form.cleaned_data["end_date"],
                    )
                except ValidationError as exc:
                    for message in exc.messages:
                        form.add_error(None, message)
                else:
                    self.message_user(
                        request,
                        f"Applied {template.name}; created {len(windows)} availability window(s).",
                    )
                    return None
        else:
            start_date = timezone.localdate(
                timezone=get_zone(template.partner.timezone),
            ) + timedelta(days=1)
            form = ApplyAvailabilityTemplateAdminForm(
                partner=template.partner,
                template=template,
                initial={
                    "start_date": start_date,
                    "end_date": start_date + timedelta(days=6),
                },
            )

        context = {
            **self.admin_site.each_context(request),
            "title": "Apply availability template",
            "opts": self.model._meta,
            "template_object": template,
            "form": form,
            "action_checkbox_name": ACTION_CHECKBOX_NAME,
            "action_name": "apply_selected_template",
            "media": self.media + form.media,
        }
        return TemplateResponse(
            request,
            "admin/schedule/apply_availability_template.html",
            context,
        )


@admin.register(AvailabilityTemplateWindow)
class AvailabilityTemplateWindowAdmin(admin.ModelAdmin):
    form = AvailabilityTemplateWindowAdminForm
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

    def save_model(self, request, obj, form, change):
        if change:
            stored = update_availability_template_window(
                actor=request.user,
                template_window=obj,
                template=form.cleaned_data["template"],
                weekday=form.cleaned_data["weekday"],
                start_time=form.cleaned_data["start_time"],
                end_time=form.cleaned_data["end_time"],
            )
        else:
            stored = create_availability_template_window(
                actor=request.user,
                template=form.cleaned_data["template"],
                weekday=form.cleaned_data["weekday"],
                start_time=form.cleaned_data["start_time"],
                end_time=form.cleaned_data["end_time"],
            )
        _copy_model_state(target=obj, source=stored)

    def delete_model(self, request, obj):
        delete_availability_template_window(actor=request.user, template_window=obj)

    def delete_queryset(self, request, queryset):
        for template_window in queryset.select_related("template__partner"):
            delete_availability_template_window(
                actor=request.user,
                template_window=template_window,
            )


def _copy_model_state(*, target, source) -> None:
    """Keep Django admin's add-form instance aligned with the service-created row."""

    for field in source._meta.concrete_fields:
        setattr(target, field.attname, getattr(source, field.attname))
    target._state.adding = False
    target._state.db = source._state.db
