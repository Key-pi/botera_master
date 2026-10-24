from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from apps.accounts.selectors import partner_ids_for_user
from apps.catalog.forms import ServiceAdminForm, ServiceCategoryAdminForm
from apps.catalog.models import Service, ServiceCategory
from apps.catalog.selectors import with_category_operational_annotations
from apps.catalog.services import (
    create_service,
    create_service_category,
    update_service,
    update_service_category,
)
from core.admin import PartnerScopedAdminMixin
from core.money import Money

CATALOG_HAS_SERVICES = "has"
CATALOG_EMPTY = "empty"
SERVICE_BOOKABLE = "bookable"
SERVICE_INACTIVE = "service_inactive"
SERVICE_CATEGORY_INACTIVE = "category_inactive"


class CategoryServiceStateFilter(admin.SimpleListFilter):
    """Find configured or empty categories using list annotations."""

    title = "services"
    parameter_name = "service_state"

    def lookups(self, request, model_admin):
        return (
            (CATALOG_HAS_SERVICES, "Has services"),
            (CATALOG_EMPTY, "Empty category"),
        )

    def queryset(self, request, queryset):
        if self.value() == CATALOG_HAS_SERVICES:
            return queryset.filter(ops_total_service_count__gt=0)
        if self.value() == CATALOG_EMPTY:
            return queryset.filter(ops_total_service_count=0)
        return queryset


class ServiceBookingStateFilter(admin.SimpleListFilter):
    """Expose why a catalog service is or is not visible to clients."""

    title = "booking visibility"
    parameter_name = "booking_state"

    def lookups(self, request, model_admin):
        return (
            (SERVICE_BOOKABLE, "Bookable"),
            (SERVICE_INACTIVE, "Service inactive"),
            (SERVICE_CATEGORY_INACTIVE, "Hidden by category"),
        )

    def queryset(self, request, queryset):
        if self.value() == SERVICE_BOOKABLE:
            return queryset.filter(is_active=True, category__is_active=True)
        if self.value() == SERVICE_INACTIVE:
            return queryset.filter(is_active=False)
        if self.value() == SERVICE_CATEGORY_INACTIVE:
            return queryset.filter(is_active=True, category__is_active=False)
        return queryset


@admin.register(ServiceCategory)
class ServiceCategoryAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    form = ServiceCategoryAdminForm
    list_display = ["name", "services_summary", "sort_order", "is_active"]
    list_filter = [
        ("partner", admin.RelatedOnlyFieldListFilter),
        "is_active",
        CategoryServiceStateFilter,
    ]
    search_fields = ["name", "description", "partner__name"]
    autocomplete_fields = ["partner"]
    readonly_fields = ["created_at", "updated_at"]
    list_per_page = 50

    def get_queryset(self, request):
        queryset = super().get_queryset(request).select_related("partner")
        return with_category_operational_annotations(queryset)

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

    @admin.display(description="Services", ordering="ops_total_service_count")
    def services_summary(self, obj):
        url = reverse("admin:catalog_service_changelist")
        query = urlencode({"category__id__exact": obj.id})
        return format_html(
            '<a href="{}?{}">{} active / {} total</a>',
            url,
            query,
            obj.ops_active_service_count,
            obj.ops_total_service_count,
        )

    def save_model(self, request, obj, form, change):
        if change:
            stored = update_service_category(
                actor=request.user,
                category=obj,
                name=form.cleaned_data["name"],
                description=form.cleaned_data["description"],
                sort_order=form.cleaned_data["sort_order"],
                is_active=form.cleaned_data["is_active"],
            )
        else:
            stored = create_service_category(
                actor=request.user,
                partner=form.cleaned_data["partner"],
                name=form.cleaned_data["name"],
                description=form.cleaned_data["description"],
                sort_order=form.cleaned_data["sort_order"],
                is_active=form.cleaned_data["is_active"],
            )
        _copy_model_state(target=obj, source=stored)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Service)
class ServiceAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    form = ServiceAdminForm
    list_display = [
        "name",
        "category",
        "price_display",
        "duration_display",
        "booking_visibility",
        "sort_order",
    ]
    list_filter = [
        ("partner", admin.RelatedOnlyFieldListFilter),
        ("category", admin.RelatedOnlyFieldListFilter),
        ServiceBookingStateFilter,
        "currency",
    ]
    search_fields = ["name", "description", "category__name", "partner__name"]
    autocomplete_fields = ["partner", "category"]
    readonly_fields = ["created_at", "updated_at"]
    list_select_related = ["partner", "category"]
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

    @admin.display(description="Price", ordering="price_amount")
    def price_display(self, obj):
        amount = Money(amount=obj.price_amount, currency=obj.currency)
        return f"{amount.to_major_decimal()} {amount.currency}"

    @admin.display(description="Duration", ordering="duration_minutes")
    def duration_display(self, obj):
        return f"{obj.duration_minutes} min"

    @admin.display(description="Booking visibility", ordering="is_active")
    def booking_visibility(self, obj):
        if not obj.is_active:
            return "Service inactive"
        if not obj.category.is_active:
            return "Hidden by category"
        return "Bookable"

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "category" and not request.user.is_superuser:
            kwargs["queryset"] = ServiceCategory.objects.filter(
                partner_id__in=partner_ids_for_user(request.user)
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        values = {
            "category": form.cleaned_data["category"],
            "name": form.cleaned_data["name"],
            "description": form.cleaned_data["description"],
            "price_amount": form.cleaned_data["price_amount"],
            "currency": form.cleaned_data["currency"],
            "duration_minutes": form.cleaned_data["duration_minutes"],
            "sort_order": form.cleaned_data["sort_order"],
            "is_active": form.cleaned_data["is_active"],
        }
        if change:
            stored = update_service(
                actor=request.user,
                service=obj,
                **values,
            )
        else:
            stored = create_service(
                actor=request.user,
                partner=form.cleaned_data["partner"],
                **values,
            )
        _copy_model_state(target=obj, source=stored)

    def has_delete_permission(self, request, obj=None):
        return False


def _copy_model_state(*, target, source) -> None:
    """Keep Django admin's form instance aligned with the service-written row."""

    for field in source._meta.concrete_fields:
        setattr(target, field.attname, getattr(source, field.attname))
    target._state.adding = False
    target._state.db = source._state.db
