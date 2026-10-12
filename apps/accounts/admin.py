from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from apps.accounts.models import PartnerMembership, User
from apps.accounts.selectors import partner_ids_for_user


@admin.register(User)
class BoteraUserAdmin(UserAdmin):
    list_display = ["username", "email", "is_staff", "is_superuser", "is_active"]
    search_fields = ["username", "email", "first_name", "last_name"]


@admin.register(PartnerMembership)
class PartnerMembershipAdmin(admin.ModelAdmin):
    list_display = ["user", "partner", "role", "is_active", "created_at"]
    list_filter = ["role", "is_active"]
    search_fields = ["user__username", "user__email", "partner__name"]
    autocomplete_fields = ["user", "partner"]

    def get_queryset(self, request):
        queryset = super().get_queryset(request).select_related("user", "partner")
        if request.user.is_superuser:
            return queryset
        return queryset.filter(partner_id__in=partner_ids_for_user(request.user))
