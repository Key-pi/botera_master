from __future__ import annotations

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext_lazy as _

from apps.accounts.selectors import partner_ids_for_user
from apps.appointments.models import Appointment
from apps.clients.models import ClientProfile
from apps.finance.forms import ExpenseAdminForm, PartnerPenaltySettingsAdminForm
from apps.finance.models import Expense, PartnerPenaltySettings, Penalty, PenaltyApplication
from apps.finance.services import (
    create_expense,
    delete_expense,
    update_expense,
    update_penalty_settings,
)
from core.admin import PartnerScopedAdminMixin
from core.money import Money


@admin.register(PartnerPenaltySettings)
class PartnerPenaltySettingsAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    form = PartnerPenaltySettingsAdminForm
    list_display = [
        "penalties_enabled",
        "late_cancel_penalty_display",
        "no_show_penalty_display",
        "late_arrival_threshold_minutes",
        "late_arrival_penalty_display",
    ]
    list_filter = ["penalties_enabled"]
    search_fields = ["partner__name"]
    readonly_fields = ["partner", "currency", "created_at", "updated_at"]

    def get_list_display(self, request):
        columns = list(super().get_list_display(request))
        if request.user.is_superuser:
            columns.insert(0, "partner")
        return columns

    @admin.display(description=_("Late cancellation"), ordering="late_cancel_penalty_amount")
    def late_cancel_penalty_display(self, obj):
        return _money_label(obj.late_cancel_penalty_amount, obj.currency)

    @admin.display(description=_("No-show"), ordering="no_show_penalty_amount")
    def no_show_penalty_display(self, obj):
        return _money_label(obj.no_show_penalty_amount, obj.currency)

    @admin.display(description=_("Late arrival"), ordering="late_arrival_penalty_amount")
    def late_arrival_penalty_display(self, obj):
        return _money_label(obj.late_arrival_penalty_amount, obj.currency)

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
        "reason",
        "status",
        "amount_display",
        "late_by_minutes",
        "source_appointment",
        "created_at",
    ]
    list_filter = [
        ("partner", admin.RelatedOnlyFieldListFilter),
        "reason",
        "status",
        "currency",
    ]
    search_fields = [
        "client_profile__name",
        "client_profile__phone",
        "client_profile__telegram_identity__username",
        "note",
    ]
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
    list_select_related = ["partner", "client_profile", "source_appointment"]
    list_per_page = 50

    def get_list_display(self, request):
        columns = list(super().get_list_display(request))
        if request.user.is_superuser:
            columns.insert(1, "partner")
        return columns

    @admin.display(description=_("Amount"), ordering="amount")
    def amount_display(self, obj):
        return _money_label(obj.amount, obj.currency)

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
        "status",
        "amount_display",
        "created_at",
    ]
    list_filter = [
        ("partner", admin.RelatedOnlyFieldListFilter),
        "status",
        "currency",
    ]
    search_fields = [
        "penalty__client_profile__name",
        "penalty__client_profile__phone",
        "penalty__client_profile__telegram_identity__username",
    ]
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
    list_select_related = ["partner", "penalty", "penalty__client_profile", "appointment"]
    list_per_page = 50

    def get_list_display(self, request):
        columns = list(super().get_list_display(request))
        if request.user.is_superuser:
            columns.insert(2, "partner")
        return columns

    @admin.display(description=_("Amount"), ordering="amount_snapshot")
    def amount_display(self, obj):
        return _money_label(obj.amount_snapshot, obj.currency)

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
    form = ExpenseAdminForm
    list_display = ["date", "description", "amount_display", "created_by"]
    list_filter = [
        ("partner", admin.RelatedOnlyFieldListFilter),
        "currency",
        "date",
    ]
    search_fields = ["description", "partner__name"]
    autocomplete_fields = ["partner"]
    readonly_fields = ["created_by", "created_at", "updated_at"]
    date_hierarchy = "date"
    list_select_related = ["partner", "created_by"]
    list_per_page = 50

    def get_list_display(self, request):
        columns = list(super().get_list_display(request))
        if request.user.is_superuser:
            columns.insert(1, "partner")
        return columns

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            readonly_fields.append("partner")
        return readonly_fields

    @admin.display(description=_("Amount"), ordering="amount")
    def amount_display(self, obj):
        return _money_label(obj.amount, obj.currency)

    def save_model(self, request, obj, form, change):
        values = {
            "date": form.cleaned_data["date"],
            "amount": form.cleaned_data["amount"],
            "currency": form.cleaned_data["currency"],
            "description": form.cleaned_data["description"],
        }
        if change:
            stored = update_expense(
                actor=request.user,
                expense=obj,
                **values,
            )
        else:
            stored = create_expense(
                actor=request.user,
                partner=form.cleaned_data["partner"],
                **values,
            )
        _copy_model_state(target=obj, source=stored)

    def delete_model(self, request, obj):
        delete_expense(actor=request.user, expense=obj)

    def delete_queryset(self, request, queryset):
        for expense in queryset.select_related("partner"):
            delete_expense(actor=request.user, expense=expense)


def _money_label(amount: int, currency: str) -> str:
    money = Money(amount=amount, currency=currency)
    return f"{money.to_major_decimal()} {money.currency}"


def _copy_model_state(*, target, source) -> None:
    """Keep the admin instance aligned with a service-updated row."""

    for field in source._meta.concrete_fields:
        setattr(target, field.attname, getattr(source, field.attname))
    target._state.adding = False
    target._state.db = source._state.db
