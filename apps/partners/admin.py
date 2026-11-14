from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _

from apps.partners.forms import PartnerOnboardingAdminForm
from apps.partners.models import Partner
from apps.partners.selectors import partner_onboarding_snapshot, visible_partners_for_user
from apps.partners.services import create_partner_with_owner, update_partner_settings
from apps.partners.setup import get_partner_setup_status


@admin.register(Partner)
class PartnerAdmin(admin.ModelAdmin):
    add_form = PartnerOnboardingAdminForm
    add_fieldsets = (
        (
            _("Partner"),
            {"fields": ("name", "timezone", "default_language")},
        ),
        (
            _("Owner account"),
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

    @admin.display(boolean=True, description=_("Setup complete"))
    def setup_complete(self, obj: Partner) -> bool:
        return get_partner_setup_status(obj).is_complete

    @admin.display(description=_("Missing setup"))
    def missing_setup_steps(self, obj: Partner) -> str:
        missing_steps = get_partner_setup_status(obj).missing_steps
        return ", ".join(missing_steps) if missing_steps else _("Ready")

    @admin.display(description=_("Onboarding checklist"))
    def onboarding_checklist(self, obj: Partner) -> str:
        onboarding = partner_onboarding_snapshot(partner=obj)
        checklist = format_html_join(
            "",
            "<div><strong>{}</strong>: {}</div>",
            (
                (step.label, _("Done") if step.is_complete else _("Pending"))
                for step in onboarding.all_steps
            ),
        )
        setup_url = f"{reverse('partners:setup')}?partner={obj.pk}"
        return format_html(
            _('{}<p><a class="button" href="{}">Open setup guide</a></p>'),
            checklist,
            setup_url,
        )

    def save_model(self, request, obj, form, change):
        if change:
            stored = update_partner_settings(
                actor=request.user,
                partner=obj,
                partner_name=form.cleaned_data["name"],
                timezone=form.cleaned_data["timezone"],
                default_language=form.cleaned_data["default_language"],
            )
            _copy_partner_state(target=obj, source=stored)
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
