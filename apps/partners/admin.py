from django.contrib import admin
from django.utils.html import format_html_join

from apps.bot.models import BotInstance, PartnerTelegramContact
from apps.partners.forms import PartnerOnboardingAdminForm
from apps.partners.models import Partner
from apps.partners.selectors import visible_partners_for_user
from apps.partners.services import create_partner_with_owner
from apps.partners.setup import SetupStep, get_partner_setup_status


@admin.register(Partner)
class PartnerAdmin(admin.ModelAdmin):
    add_form = PartnerOnboardingAdminForm
    add_fieldsets = (
        (
            "Partner",
            {"fields": ("name", "timezone", "default_language")},
        ),
        (
            "Owner account",
            {
                "fields": (
                    "owner_username",
                    "owner_email",
                    "owner_first_name",
                    "owner_last_name",
                    "owner_password1",
                    "owner_password2",
                )
            },
        ),
    )
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
    readonly_fields = [
        "status",
        "onboarding_checklist",
        "missing_setup_steps",
        "created_at",
        "updated_at",
    ]

    def get_form(self, request, obj=None, change=False, **kwargs):
        if obj is None:
            kwargs["form"] = self.add_form
        return super().get_form(request, obj, change, **kwargs)

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            return self.add_fieldsets
        return super().get_fieldsets(request, obj)

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

    @admin.display(description="Onboarding checklist")
    def onboarding_checklist(self, obj: Partner) -> str:
        setup_status = get_partner_setup_status(obj)
        missing_steps = set(setup_status.missing_steps)
        bot_instance = BotInstance.objects.filter(partner=obj).first()
        bot_connection_checked = bool(
            bot_instance
            and bot_instance.last_checked_at
            and bot_instance.telegram_bot_id
            and bot_instance.telegram_username
            and not bot_instance.last_error_code
        )
        master_contact_bound = PartnerTelegramContact.objects.filter(
            partner=obj,
            is_active=True,
            is_primary=True,
        ).exists()
        bot_launched = bool(
            bot_instance
            and bot_instance.status == BotInstance.Status.ACTIVE
            and obj.status == Partner.Status.ACTIVE
        )
        steps = (
            ("Bot token", SetupStep.BOT_TOKEN not in missing_steps),
            ("Language", SetupStep.LANGUAGE not in missing_steps),
            ("Timezone", SetupStep.TIMEZONE not in missing_steps),
            ("Category and service", SetupStep.CATALOG_SERVICE not in missing_steps),
            ("Availability window", SetupStep.AVAILABILITY_WINDOW not in missing_steps),
            (
                "Booking confirmation mode",
                SetupStep.BOOKING_CONFIRMATION_MODE not in missing_steps,
            ),
            ("Telegram connection checked", bot_connection_checked),
            ("Master Telegram contact", master_contact_bound),
            ("Bot launched", bot_launched),
        )
        return format_html_join(
            "",
            "<div><strong>{}</strong>: {}</div>",
            ((label, "Done" if complete else "Pending") for label, complete in steps),
        )

    def save_model(self, request, obj, form, change):
        if change:
            super().save_model(request, obj, form, change)
            return

        created = create_partner_with_owner(
            actor=request.user,
            partner_name=form.cleaned_data["name"],
            owner_username=form.cleaned_data["owner_username"],
            owner_password=form.cleaned_data["owner_password2"],
            owner_email=form.cleaned_data["owner_email"],
            owner_first_name=form.cleaned_data["owner_first_name"],
            owner_last_name=form.cleaned_data["owner_last_name"],
            timezone=form.cleaned_data["timezone"],
            default_language=form.cleaned_data["default_language"],
        )
        _copy_partner_state(target=obj, source=created.partner)

    def has_add_permission(self, request):
        return request.user.is_superuser and super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser and super().has_delete_permission(request, obj)


def _copy_partner_state(*, target: Partner, source: Partner) -> None:
    """Keep Django admin's add-form instance aligned with the service-created row."""

    for field in source._meta.concrete_fields:
        setattr(target, field.attname, getattr(source, field.attname))
    target._state.adding = False
    target._state.db = source._state.db
