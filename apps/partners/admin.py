from django.contrib import admin

from apps.partners.models import Partner
from apps.partners.selectors import visible_partners_for_user
from apps.partners.setup import get_partner_setup_status


@admin.register(Partner)
class PartnerAdmin(admin.ModelAdmin):
    list_display = ["name", "status", "timezone", "default_language", "setup_complete"]
    list_filter = ["status", "default_language", "timezone"]
    search_fields = ["name"]

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        visible_ids = visible_partners_for_user(request.user).values_list("id", flat=True)
        return queryset.filter(id__in=visible_ids)

    @admin.display(boolean=True, description="Setup complete")
    def setup_complete(self, obj: Partner) -> bool:
        return get_partner_setup_status(obj).is_complete
