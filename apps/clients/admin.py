from __future__ import annotations

from django.contrib import admin

from apps.clients.models import ClientProfile, TelegramIdentity
from core.admin import PartnerScopedAdminMixin
from core.security.masking import mask_phone


@admin.register(TelegramIdentity)
class TelegramIdentityAdmin(admin.ModelAdmin):
    list_display = [
        "telegram_user_id",
        "username",
        "first_name",
        "last_name",
        "is_premium",
        "language_code",
        "last_seen_at",
    ]
    search_fields = ["=telegram_user_id", "username", "first_name", "last_name"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(ClientProfile)
class ClientProfileAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = [
        "display_name",
        "partner",
        "masked_phone",
        "language",
        "telegram_identity",
        "marketing_opt_out",
        "bot_unreachable",
    ]
    list_filter = ["partner", "language", "marketing_opt_out", "bot_unreachable"]
    search_fields = ["name", "phone", "telegram_identity__username"]
    autocomplete_fields = ["partner", "telegram_identity"]
    readonly_fields = ["created_at", "updated_at"]

    @admin.display(description="Phone")
    def masked_phone(self, obj: ClientProfile) -> str:
        return mask_phone(obj.phone) if obj.phone else ""
