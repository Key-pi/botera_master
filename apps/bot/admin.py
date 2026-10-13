from django.contrib import admin, messages

from apps.accounts.selectors import partner_ids_for_user
from apps.bot.models import BotInstance, PartnerTemplateOverride, SystemTemplate
from apps.bot.services import check_bot_connection, seed_system_templates
from core.admin import PartnerScopedAdminMixin


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
    actions = ["check_connection"]

    def get_queryset(self, request):
        queryset = super().get_queryset(request).select_related("partner")
        if request.user.is_superuser:
            return queryset
        return queryset.filter(partner_id__in=partner_ids_for_user(request.user))

    @admin.action(description="Check Telegram connection")
    def check_connection(self, request, queryset):
        checked_count = 0
        failed_count = 0
        for bot_instance in queryset:
            check_bot_connection(bot_instance, actor=request.user)
            bot_instance.refresh_from_db(fields=["last_error_code"])
            checked_count += 1
            if bot_instance.last_error_code:
                failed_count += 1

        if failed_count:
            self.message_user(
                request,
                f"Checked {checked_count} bot(s), {failed_count} need attention.",
                level=messages.WARNING,
            )
            return

        self.message_user(request, f"Checked {checked_count} bot(s).")


@admin.register(SystemTemplate)
class SystemTemplateAdmin(admin.ModelAdmin):
    list_display = ["key", "language", "content_type", "editable", "is_required"]
    list_filter = ["language", "content_type", "editable", "is_required"]
    search_fields = ["key", "body"]
    readonly_fields = ["created_at", "updated_at"]
    actions = ["sync_registry_defaults"]

    @admin.action(description="Sync system templates from registry")
    def sync_registry_defaults(self, request, queryset):
        count = seed_system_templates()
        self.message_user(request, f"Synchronized {count} system template rows.")


@admin.register(PartnerTemplateOverride)
class PartnerTemplateOverrideAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = ["partner", "key", "language", "is_active", "last_validated_at"]
    list_filter = ["language", "is_active", "partner"]
    search_fields = ["partner__name", "key", "body"]
    autocomplete_fields = ["partner"]
    readonly_fields = ["last_validated_at", "created_at", "updated_at"]
