from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils.html import format_html, format_html_join

from apps.accounts.selectors import partner_ids_for_user
from apps.bot.forms import BotInstanceAdminForm
from apps.bot.models import (
    BotInstance,
    PartnerTelegramContact,
    PartnerTelegramContactLink,
    PartnerTemplateOverride,
    SystemTemplate,
)
from apps.bot.services import (
    activate_partner_bot,
    check_bot_connection,
    create_master_contact_link,
    seed_system_templates,
    set_bot_status,
    set_bot_token,
)
from apps.partners.selectors import visible_partners_for_user
from core.admin import PartnerScopedAdminMixin


@admin.register(BotInstance)
class BotInstanceAdmin(admin.ModelAdmin):
    form = BotInstanceAdminForm
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
        "new_token",
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
    actions = [
        "check_connection",
        "launch_bots",
        "pause_bots",
        "create_master_contact_links",
    ]

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            readonly_fields.append("partner")
        return readonly_fields

    def get_queryset(self, request):
        queryset = super().get_queryset(request).select_related("partner")
        if request.user.is_superuser:
            return queryset
        return queryset.filter(partner_id__in=partner_ids_for_user(request.user))

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "partner":
            kwargs["queryset"] = visible_partners_for_user(request.user).filter(
                bot_instance__isnull=True
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def has_add_permission(self, request):
        return (
            super().has_add_permission(request)
            and visible_partners_for_user(request.user).filter(bot_instance__isnull=True).exists()
        )

    def save_model(self, request, obj, form, change):
        raw_token = form.cleaned_data.get("new_token", "")
        if not raw_token:
            super().save_model(request, obj, form, change)
            return

        stored = set_bot_token(
            actor=request.user,
            partner=obj.partner,
            raw_token=raw_token,
        )
        _copy_model_state(target=obj, source=stored)

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

    @admin.action(description="Launch selected bots after setup checks")
    def launch_bots(self, request, queryset):
        launched_count = 0
        failures: list[str] = []
        for bot_instance in queryset:
            try:
                activate_partner_bot(actor=request.user, bot_instance=bot_instance)
            except (PermissionDenied, ValidationError) as exc:
                failures.append(f"{bot_instance.partner}: {_error_text(exc)}")
            else:
                launched_count += 1

        if launched_count:
            self.message_user(request, f"Launched {launched_count} bot(s).")
        if failures:
            self.message_user(request, " | ".join(failures), level=messages.WARNING)

    @admin.action(description="Pause selected bots")
    def pause_bots(self, request, queryset):
        paused_count = 0
        failures: list[str] = []
        for bot_instance in queryset:
            try:
                set_bot_status(
                    actor=request.user,
                    bot_instance=bot_instance,
                    status=BotInstance.Status.PAUSED,
                )
            except (PermissionDenied, ValidationError) as exc:
                failures.append(f"{bot_instance.partner}: {_error_text(exc)}")
            else:
                paused_count += 1

        if paused_count:
            self.message_user(request, f"Paused {paused_count} bot(s).")
        if failures:
            self.message_user(request, " | ".join(failures), level=messages.WARNING)

    @admin.action(description="Create my master Telegram link")
    def create_master_contact_links(self, request, queryset):
        links = []
        failures: list[str] = []
        for bot_instance in queryset:
            try:
                link = create_master_contact_link(
                    actor=request.user,
                    partner=bot_instance.partner,
                )
            except (PermissionDenied, ValidationError) as exc:
                failures.append(f"{bot_instance.partner}: {_error_text(exc)}")
            else:
                links.append((bot_instance.partner.name, link.url, link.expires_at))

        if links:
            rendered_links = format_html_join(
                "",
                '<li><a href="{}" target="_blank" rel="noopener">{}</a> (expires {})</li>',
                ((url, partner_name, expires_at) for partner_name, url, expires_at in links),
            )
            self.message_user(
                request,
                format_html(
                    "Open the signed link in the master's Telegram:<ul>{}</ul>",
                    rendered_links,
                ),
            )
        if failures:
            self.message_user(request, " | ".join(failures), level=messages.WARNING)


@admin.register(PartnerTelegramContact)
class PartnerTelegramContactAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = [
        "partner",
        "telegram_identity",
        "user",
        "role",
        "is_primary",
        "is_active",
        "last_delivery_failure_code",
    ]
    list_filter = ["role", "is_primary", "is_active", "partner"]
    search_fields = [
        "partner__name",
        "telegram_identity__username",
        "telegram_identity__telegram_user_id",
        "user__username",
    ]
    readonly_fields = [
        "partner",
        "telegram_identity",
        "user",
        "linked_at",
        "last_delivery_failure_code",
        "last_delivery_failure_at",
        "created_at",
        "updated_at",
    ]

    def has_add_permission(self, request):
        return False


@admin.register(PartnerTelegramContactLink)
class PartnerTelegramContactLinkAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = ["partner", "user", "expires_at", "consumed_at", "created_at"]
    list_filter = ["partner", "expires_at", "consumed_at"]
    search_fields = ["partner__name", "user__username"]
    readonly_fields = [
        "partner",
        "user",
        "nonce_fingerprint",
        "expires_at",
        "consumed_at",
        "created_at",
        "updated_at",
    ]

    def has_add_permission(self, request):
        return False


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


def _copy_model_state(*, target: BotInstance, source: BotInstance) -> None:
    """Keep Django admin's form object aligned with the service-saved row."""

    for field in source._meta.concrete_fields:
        setattr(target, field.attname, getattr(source, field.attname))
    target._state.adding = False
    target._state.db = source._state.db


def _error_text(exc: PermissionDenied | ValidationError) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(exc.messages)
    return str(exc)
