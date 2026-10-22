from __future__ import annotations

from django.contrib import admin
from django.core.exceptions import PermissionDenied

from apps.accounts.selectors import partner_ids_for_user
from apps.appointments.models import Appointment
from apps.clients.models import ClientProfile
from apps.finance.models import Expense, PartnerPenaltySettings, Penalty, PenaltyApplication
from apps.finance.services import update_penalty_settings
from core.admin import PartnerScopedAdminMixin


@admin.register(PartnerPenaltySettings)
class PartnerPenaltySettingsAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = [
        "partner",
        "penalties_enabled",
        "late_cancel_penalty_amount",
        "no_show_penalty_amount",
        "late_arrival_threshold_minutes",
        "late_arrival_penalty_amount",
        "currency",
    ]
    list_filter = ["penalties_enabled", "currency"]
    search_fields = ["partner__name"]
    readonly_fields = ["partner", "currency", "created_at", "updated_at"]

    def save_model(self, request, obj, form, change):
        if not change:
            raise PermissionDenied("Penalty settings are created during partner onboarding.")
        stored = update_penalty_settings(
            actor=request.user,
            partner=obj.partner,
            penalties_enabled=form.cleaned_data["penalties_enabled"],
            late_cancel_penalty_amount=form.cleaned_data["late_cancel_penalty_amount"],
            no_show_penalty_amount=form.cleaned_data["no_show_penalty_amount"],
            late_arrival_threshold_minutes=form.cleaned_data["late_arrival_threshold_minutes"],
            late_arrival_penalty_amount=form.cleaned_data["late_arrival_penalty_amount"],
        )
        _copy_model_state(target=obj, source=stored)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Penalty)
class PenaltyAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = [
        "client_profile",
        "partner",
        "reason",
        "status",
        "amount",
        "currency",
        "late_by_minutes",
        "source_appointment",
        "created_at",
    ]
    list_filter = ["partner", "reason", "status", "currency"]
    search_fields = ["client_profile__name", "client_profile__phone", "note"]
    readonly_fields = [
        "partner",
        "client_profile",
        "source_appointment",
        "reason",
        "status",
        "amount",
        "currency",
        "late_by_minutes",
        "note",
        "created_by",
        "paid_at",
        "waived_at",
        "created_at",
        "updated_at",
    ]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser:
            partner_ids = partner_ids_for_user(request.user)
            if db_field.name == "client_profile":
                kwargs["queryset"] = ClientProfile.objects.filter(partner_id__in=partner_ids)
            elif db_field.name == "source_appointment":
                kwargs["queryset"] = Appointment.objects.filter(partner_id__in=partner_ids)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PenaltyApplication)
class PenaltyApplicationAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = [
        "penalty",
        "appointment",
        "partner",
        "status",
        "amount_snapshot",
        "currency",
        "created_at",
    ]
    list_filter = ["partner", "status", "currency"]
    search_fields = ["penalty__client_profile__name", "penalty__client_profile__phone"]
    readonly_fields = [
        "partner",
        "penalty",
        "appointment",
        "amount_snapshot",
        "currency",
        "status",
        "released_at",
        "settled_at",
        "created_at",
        "updated_at",
    ]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser:
            partner_ids = partner_ids_for_user(request.user)
            if db_field.name == "penalty":
                kwargs["queryset"] = Penalty.objects.filter(partner_id__in=partner_ids)
            elif db_field.name == "appointment":
                kwargs["queryset"] = Appointment.objects.filter(partner_id__in=partner_ids)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Expense)
class ExpenseAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = ["date", "partner", "amount", "currency", "description", "created_by"]
    list_filter = ["partner", "currency", "date"]
    search_fields = ["description"]
    autocomplete_fields = ["partner", "created_by"]
    readonly_fields = ["created_at", "updated_at"]


def _copy_model_state(*, target, source) -> None:
    """Keep the admin instance aligned with a service-updated row."""

    for field in source._meta.concrete_fields:
        setattr(target, field.attname, getattr(source, field.attname))
    target._state.adding = False
    target._state.db = source._state.db
