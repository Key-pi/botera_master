from __future__ import annotations

from django.contrib import admin

from apps.accounts.selectors import partner_ids_for_user
from apps.appointments.models import Appointment
from apps.clients.models import ClientProfile
from apps.finance.models import Expense, PartnerPenaltySettings, Penalty, PenaltyApplication
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
    autocomplete_fields = ["partner"]


@admin.register(Penalty)
class PenaltyAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = [
        "client_profile",
        "partner",
        "reason",
        "status",
        "amount",
        "currency",
        "source_appointment",
        "created_at",
    ]
    list_filter = ["partner", "reason", "status", "currency"]
    search_fields = ["client_profile__name", "client_profile__phone", "note"]
    autocomplete_fields = ["partner", "client_profile", "source_appointment", "created_by"]
    readonly_fields = ["created_at", "updated_at", "paid_at", "waived_at"]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser:
            partner_ids = partner_ids_for_user(request.user)
            if db_field.name == "client_profile":
                kwargs["queryset"] = ClientProfile.objects.filter(partner_id__in=partner_ids)
            elif db_field.name == "source_appointment":
                kwargs["queryset"] = Appointment.objects.filter(partner_id__in=partner_ids)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


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
    autocomplete_fields = ["partner", "penalty", "appointment"]
    readonly_fields = ["created_at", "updated_at", "released_at", "settled_at"]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser:
            partner_ids = partner_ids_for_user(request.user)
            if db_field.name == "penalty":
                kwargs["queryset"] = Penalty.objects.filter(partner_id__in=partner_ids)
            elif db_field.name == "appointment":
                kwargs["queryset"] = Appointment.objects.filter(partner_id__in=partner_ids)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Expense)
class ExpenseAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = ["date", "partner", "amount", "currency", "description", "created_by"]
    list_filter = ["partner", "currency", "date"]
    search_fields = ["description"]
    autocomplete_fields = ["partner", "created_by"]
    readonly_fields = ["created_at", "updated_at"]
