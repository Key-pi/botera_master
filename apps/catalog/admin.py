from __future__ import annotations

from django.contrib import admin

from apps.accounts.selectors import partner_ids_for_user
from apps.catalog.models import Service, ServiceCategory
from core.admin import PartnerScopedAdminMixin


@admin.register(ServiceCategory)
class ServiceCategoryAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = ["name", "partner", "sort_order", "is_active"]
    list_filter = ["partner", "is_active"]
    search_fields = ["name", "description"]
    autocomplete_fields = ["partner"]


@admin.register(Service)
class ServiceAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = [
        "name",
        "partner",
        "category",
        "price_amount",
        "currency",
        "duration_minutes",
        "sort_order",
        "is_active",
    ]
    list_filter = ["partner", "category", "currency", "is_active"]
    search_fields = ["name", "description", "category__name"]
    autocomplete_fields = ["partner", "category"]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "category" and not request.user.is_superuser:
            kwargs["queryset"] = ServiceCategory.objects.filter(
                partner_id__in=partner_ids_for_user(request.user)
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
