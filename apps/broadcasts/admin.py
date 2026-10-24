from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils.html import format_html

from apps.accounts.selectors import partner_ids_for_user
from apps.broadcasts.forms import (
    BroadcastCampaignAdminForm,
    BroadcastTemplateAdminForm,
    campaign_form_initial_from_template,
)
from apps.broadcasts.models import BroadcastCampaign, BroadcastRecipient, BroadcastTemplate
from apps.broadcasts.services import (
    cancel_broadcast_campaign,
    create_broadcast_campaign,
    create_broadcast_template,
    delete_broadcast_template,
    queue_broadcast_campaign,
    update_broadcast_campaign,
    update_broadcast_template,
)
from apps.catalog.models import Service
from apps.partners.models import Partner
from core.admin import PartnerScopedAdminMixin

EDITABLE_CAMPAIGN_STATUSES = {
    BroadcastCampaign.Status.DRAFT,
    BroadcastCampaign.Status.SCHEDULED,
}


@admin.register(BroadcastCampaign)
class BroadcastCampaignAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    form = BroadcastCampaignAdminForm
    change_list_template = "admin/broadcasts/broadcastcampaign/change_list.html"
    list_display = [
        "content_summary",
        "status",
        "progress_display",
        "audience_display",
        "scheduled_at",
        "created_at",
    ]
    list_filter = [
        ("partner", admin.RelatedOnlyFieldListFilter),
        "status",
        "scheduled_at",
    ]
    search_fields = ["text", "photo", "partner__name"]
    autocomplete_fields = ["partner"]
    readonly_fields = [
        "status",
        "created_by",
        "queued_count",
        "sent_count",
        "failed_count",
        "skipped_count",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
        "content_detail",
        "audience_display",
        "buttons_display",
        "progress_display",
    ]
    actions = ["start_selected_campaigns", "cancel_selected_campaigns"]
    list_select_related = ["partner", "created_by"]
    list_per_page = 50

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("partner", "created_by")

    def get_list_display(self, request):
        columns = list(super().get_list_display(request))
        if request.user.is_superuser:
            columns.insert(1, "partner")
        return columns

    def get_fieldsets(self, request, obj=None):
        if obj is not None and obj.status not in EDITABLE_CAMPAIGN_STATUSES:
            return (
                (
                    "Campaign",
                    {
                        "fields": (
                            "partner",
                            "status",
                            "content_detail",
                            "audience_display",
                            "buttons_display",
                            "progress_display",
                            "scheduled_at",
                            "started_at",
                            "finished_at",
                            "created_by",
                            "created_at",
                            "updated_at",
                        )
                    },
                ),
            )
        return (
            ("Content", {"fields": ("partner", "text", "photo")}),
            ("Audience", {"fields": ("audience", "service", "long_absent_days")}),
            (
                "Buttons",
                {
                    "fields": (
                        "include_book_button",
                        "book_button_label",
                        "include_my_appointments_button",
                        "my_appointments_button_label",
                        "external_link_label",
                        "external_link_url",
                    )
                },
            ),
            ("Delivery", {"fields": ("delivery_mode", "scheduled_at")}),
        )

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            readonly_fields.append("partner")
        return readonly_fields

    def get_form(self, request, obj=None, change=False, **kwargs):
        form_class = super().get_form(request, obj, change, **kwargs)
        allowed_partner_ids = tuple(_visible_partner_ids(request.user))

        class RequestScopedBroadcastCampaignForm(form_class):
            def __init__(self, *args, **form_kwargs):
                form_kwargs["allowed_partner_ids"] = allowed_partner_ids
                super().__init__(*args, **form_kwargs)

        return RequestScopedBroadcastCampaignForm

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        partner_ids = tuple(_visible_partner_ids(request.user))
        if "partner" not in initial and len(partner_ids) == 1:
            initial["partner"] = partner_ids[0]

        template_id = request.GET.get("template", "").strip()
        if template_id:
            template = BroadcastTemplate.objects.filter(
                id=template_id,
                partner_id__in=partner_ids,
            ).first()
            if template is not None:
                initial.update(campaign_form_initial_from_template(template))
        return initial

    @admin.display(description="Message")
    def content_summary(self, obj):
        if obj.text:
            return obj.text[:80] + ("…" if len(obj.text) > 80 else "")
        return f"Photo: {obj.photo[:64]}"

    @admin.display(description="Content")
    def content_detail(self, obj):
        parts = []
        if obj.text:
            parts.append(obj.text)
        if obj.photo:
            parts.append(f"Photo: {obj.photo}")
        return "\n".join(parts) or "No content"

    @admin.display(description="Audience")
    def audience_display(self, obj):
        filters = obj.filters or {}
        if filters.get("service_id"):
            service_name = (
                Service.objects.filter(
                    partner=obj.partner,
                    id=filters["service_id"],
                )
                .values_list("name", flat=True)
                .first()
            )
            return f"Completed service: {service_name or 'removed service'}"
        if filters.get("long_absent_days"):
            return f"Absent for {filters['long_absent_days']}+ days"
        if filters.get("has_future_appointment") is True:
            return "Has future appointment"
        if filters.get("without_future_appointment") is True:
            return "Without future appointment"
        return "All reachable subscribed clients"

    @admin.display(description="Buttons")
    def buttons_display(self, obj):
        labels = [button.get("label", "") for button in (obj.buttons or [])]
        return ", ".join(label for label in labels if label) or "None"

    @admin.display(description="Progress")
    def progress_display(self, obj):
        total = obj.queued_count + obj.sent_count + obj.failed_count + obj.skipped_count
        if not total:
            return "0 recipients" if obj.status != BroadcastCampaign.Status.DRAFT else "Not started"
        finished = obj.sent_count + obj.failed_count + obj.skipped_count
        percent = round(finished * 100 / total)
        label = (
            f"{percent}% · {obj.sent_count} sent · {obj.failed_count} failed · "
            f"{obj.skipped_count} skipped · {obj.queued_count} pending"
        )
        url = reverse("admin:broadcasts_broadcastrecipient_changelist")
        query = urlencode({"campaign__id__exact": obj.id})
        return format_html('<a href="{}?{}">{}</a>', url, query, label)

    def save_model(self, request, obj, form, change):
        values = {
            "text": form.cleaned_data["text"],
            "photo": form.cleaned_data["photo"],
            "buttons": form.cleaned_data["buttons"],
            "filters": form.cleaned_data["filters"],
            "scheduled_at": form.cleaned_data["scheduled_at"],
        }
        if change:
            if obj.status not in EDITABLE_CAMPAIGN_STATUSES:
                raise PermissionDenied("A started broadcast cannot be edited.")
            stored = update_broadcast_campaign(
                actor=request.user,
                campaign=obj,
                **values,
            )
        else:
            stored = create_broadcast_campaign(
                actor=request.user,
                partner=form.cleaned_data["partner"],
                **values,
            )
        if form.cleaned_data["start_immediately"]:
            stored = queue_broadcast_campaign(actor=request.user, campaign=stored)
        _copy_model_state(target=obj, source=stored)

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.status not in EDITABLE_CAMPAIGN_STATUSES:
            return False
        return super().has_change_permission(request, obj)

    @admin.action(description="Start selected campaigns now")
    def start_selected_campaigns(self, request, queryset):
        started = 0
        rejected = 0
        for campaign in queryset.select_related("partner"):
            if campaign.status not in EDITABLE_CAMPAIGN_STATUSES:
                rejected += 1
                continue
            try:
                queue_broadcast_campaign(actor=request.user, campaign=campaign)
            except ValidationError:
                rejected += 1
            else:
                started += 1
        if started:
            self.message_user(request, f"Started {started} broadcast campaign(s).")
        if rejected:
            self.message_user(
                request,
                f"Could not start {rejected} campaign(s) from their current status.",
                level=messages.WARNING,
            )

    @admin.action(description="Cancel selected campaigns")
    def cancel_selected_campaigns(self, request, queryset):
        cancelled = 0
        rejected = 0
        for campaign in queryset.select_related("partner"):
            if campaign.status in {
                BroadcastCampaign.Status.CANCELLED,
                BroadcastCampaign.Status.COMPLETED,
                BroadcastCampaign.Status.FAILED_PARTIAL,
            }:
                rejected += 1
                continue
            try:
                cancel_broadcast_campaign(actor=request.user, campaign=campaign)
            except ValidationError:
                rejected += 1
            else:
                cancelled += 1
        if cancelled:
            self.message_user(request, f"Cancelled {cancelled} broadcast campaign(s).")
        if rejected:
            self.message_user(
                request,
                f"Could not cancel {rejected} finished campaign(s).",
                level=messages.WARNING,
            )

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BroadcastRecipient)
class BroadcastRecipientAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = [
        "campaign",
        "client_profile",
        "status",
        "attempt_count",
        "next_attempt_at",
        "sent_at",
    ]
    list_filter = [
        ("partner", admin.RelatedOnlyFieldListFilter),
        ("campaign", admin.RelatedOnlyFieldListFilter),
        "status",
    ]
    search_fields = [
        "campaign__text",
        "client_profile__name",
        "client_profile__phone",
        "client_profile__telegram_identity__username",
    ]
    readonly_fields = [field.name for field in BroadcastRecipient._meta.fields]
    list_select_related = [
        "partner",
        "campaign",
        "client_profile",
        "client_profile__telegram_identity",
    ]
    list_per_page = 50

    def get_list_display(self, request):
        columns = list(super().get_list_display(request))
        if request.user.is_superuser:
            columns.insert(1, "partner")
        return columns

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BroadcastTemplate)
class BroadcastTemplateAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    form = BroadcastTemplateAdminForm
    list_display = ["name", "content_summary", "buttons_display", "create_campaign_link"]
    list_filter = [("partner", admin.RelatedOnlyFieldListFilter)]
    search_fields = ["partner__name", "name", "text"]
    autocomplete_fields = ["partner"]
    readonly_fields = ["created_by", "created_at", "updated_at"]
    list_select_related = ["partner", "created_by"]
    list_per_page = 50

    def get_list_display(self, request):
        columns = list(super().get_list_display(request))
        if request.user.is_superuser:
            columns.insert(1, "partner")
        return columns

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            readonly_fields.append("partner")
        return readonly_fields

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        partner_ids = tuple(_visible_partner_ids(request.user))
        if "partner" not in initial and len(partner_ids) == 1:
            initial["partner"] = partner_ids[0]
        return initial

    @admin.display(description="Message")
    def content_summary(self, obj):
        if obj.text:
            return obj.text[:80] + ("…" if len(obj.text) > 80 else "")
        return f"Photo: {obj.photo[:64]}"

    @admin.display(description="Buttons")
    def buttons_display(self, obj):
        labels = [button.get("label", "") for button in (obj.buttons or [])]
        return ", ".join(label for label in labels if label) or "None"

    @admin.display(description="Reuse")
    def create_campaign_link(self, obj):
        url = reverse("admin:broadcasts_broadcastcampaign_add")
        query = urlencode({"partner": obj.partner_id, "template": obj.id})
        return format_html('<a href="{}?{}">Create campaign</a>', url, query)

    def save_model(self, request, obj, form, change):
        values = {
            "name": form.cleaned_data["name"],
            "text": form.cleaned_data["text"],
            "photo": form.cleaned_data["photo"],
            "buttons": form.cleaned_data["buttons"],
        }
        if change:
            stored = update_broadcast_template(
                actor=request.user,
                template=obj,
                **values,
            )
        else:
            stored = create_broadcast_template(
                actor=request.user,
                partner=form.cleaned_data["partner"],
                **values,
            )
        _copy_model_state(target=obj, source=stored)

    def delete_model(self, request, obj):
        delete_broadcast_template(actor=request.user, template=obj)

    def delete_queryset(self, request, queryset):
        for template in queryset.select_related("partner"):
            delete_broadcast_template(actor=request.user, template=template)


def _visible_partner_ids(user):
    if user.is_superuser:
        return Partner.objects.order_by("id").values_list("id", flat=True)
    return partner_ids_for_user(user)


def _copy_model_state(*, target, source) -> None:
    """Keep Django admin's form instance aligned with a service-written row."""

    for field in source._meta.concrete_fields:
        setattr(target, field.attname, getattr(source, field.attname))
    target._state.adding = False
    target._state.db = source._state.db
