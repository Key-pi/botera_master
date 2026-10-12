from django.contrib import admin

from apps.accounts.selectors import partner_ids_for_user
from apps.bot.models import BotInstance


@admin.register(BotInstance)
class BotInstanceAdmin(admin.ModelAdmin):
    list_display = [
        "partner",
        "status",
        "delivery_mode",
        "token_masked",
        "telegram_username",
        "last_activity_at",
        "last_error_code",
    ]
    list_filter = ["status", "delivery_mode"]
    search_fields = ["partner__name", "telegram_username"]
    fields = [
        "partner",
        "status",
        "delivery_mode",
        "token_masked",
        "telegram_bot_id",
        "telegram_username",
        "last_error_code",
        "last_error_message_masked",
        "last_checked_at",
        "last_started_at",
        "last_activity_at",
        "created_at",
        "updated_at",
    ]
    readonly_fields = [
        "partner",
        "status",
        "delivery_mode",
        "token_masked",
        "telegram_bot_id",
        "telegram_username",
        "last_error_code",
        "last_error_message_masked",
        "last_checked_at",
        "last_started_at",
        "last_activity_at",
        "created_at",
        "updated_at",
    ]

    def get_queryset(self, request):
        queryset = super().get_queryset(request).select_related("partner")
        if request.user.is_superuser:
            return queryset
        return queryset.filter(partner_id__in=partner_ids_for_user(request.user))
