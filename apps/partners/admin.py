from django.contrib import admin

from apps.partners.models import Partner
from apps.partners.selectors import visible_partners_for_user
from apps.partners.setup import get_partner_setup_status


@admin.register(Partner)
class PartnerAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "status",
        "timezone",
        "default_language",
        "setup_complete",
        "missing_setup_steps",
    ]
    list_filter = ["status", "default_language", "timezone"]
    search_fields = ["name"]
    readonly_fields = ["status", "missing_setup_steps", "created_at", "updated_at"]

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        visible_ids = visible_partners_for_user(request.user).values_list("id", flat=True)
        return queryset.filter(id__in=visible_ids)

    @admin.display(boolean=True, description="Setup complete")
    def setup_complete(self, obj: Partner) -> bool:
        return get_partner_setup_status(obj).is_complete

    @admin.display(description="Missing setup")
    def missing_setup_steps(self, obj: Partner) -> str:
        missing_steps = get_partner_setup_status(obj).missing_steps
        return ", ".join(missing_steps) if missing_steps else "Ready"

    def has_add_permission(self, request):
        return request.user.is_superuser and super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser and super().has_delete_permission(request, obj)
