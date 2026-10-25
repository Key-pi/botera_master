from __future__ import annotations

import json

from django.contrib import admin
from django.db.models import Q
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html, format_html_join

from apps.audit.models import AuditEvent, PlatformDiagnostic, WorkerHeartbeat
from apps.audit.presentation import (
    ACTIVITY_CATEGORY_CHOICES,
    ACTIVITY_CATEGORY_PREFIXES,
    activity_actor_label,
    activity_category_label,
    activity_detail_rows,
    activity_target_presentation,
    activity_title,
)
from apps.audit.selectors import audit_events_visible_to_user, platform_diagnostics_visible_to_user
from core.admin import PartnerScopedAdminMixin
from core.timezones import to_partner_time


class ActivityCategoryFilter(admin.SimpleListFilter):
    """Group technical event keys into partner-facing business areas."""

    title = "activity area"
    parameter_name = "activity_area"

    def lookups(self, request, model_admin):
        return ACTIVITY_CATEGORY_CHOICES

    def queryset(self, request, queryset):
        selected = self.value()
        if not selected:
            return queryset
        if selected == "other":
            known = Q()
            for prefixes in ACTIVITY_CATEGORY_PREFIXES.values():
                for prefix in prefixes:
                    known |= Q(event_type__startswith=prefix)
            return queryset.exclude(known)
        prefixes = ACTIVITY_CATEGORY_PREFIXES.get(selected, ())
        category_query = Q()
        for prefix in prefixes:
            category_query |= Q(event_type__startswith=prefix)
        return queryset.filter(category_query) if prefixes else queryset.none()


@admin.register(AuditEvent)
class AuditEventAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = [
        "activity_title_display",
        "category_display",
        "summary_preview",
        "subject_display",
        "actor_display",
        "created_at_partner_time",
    ]
    list_display_links = ["activity_title_display"]
    search_fields = [
        "partner__name",
        "event_type",
        "target_type",
        "target_id",
        "message",
        "reason",
        "actor__username",
        "actor__first_name",
        "actor__last_name",
    ]
    readonly_fields = [
        "activity_title_display",
        "category_display",
        "message_display",
        "reason_display",
        "business_details_display",
        "subject_display",
        "actor_display",
        "created_at_partner_time",
        "retain_until_partner_time",
        "technical_identity_display",
        "masked_payload_display",
    ]
    date_hierarchy = "created_at"
    ordering = ["-created_at"]
    list_per_page = 50
    show_full_result_count = False
    empty_value_display = "-"

    def get_queryset(self, request):
        return audit_events_visible_to_user(request.user)

    def get_list_display(self, request):
        columns = list(super().get_list_display(request))
        if request.user.is_superuser:
            columns.insert(2, "partner")
        return columns

    def get_list_filter(self, request):
        filters = [ActivityCategoryFilter, ("actor", admin.RelatedOnlyFieldListFilter)]
        if request.user.is_superuser:
            filters.insert(0, ("partner", admin.RelatedOnlyFieldListFilter))
        return filters

    def get_fieldsets(self, request, obj=None):
        fieldsets = [
            (
                "Activity",
                {
                    "fields": (
                        "activity_title_display",
                        "category_display",
                        "message_display",
                        "reason_display",
                    )
                },
            ),
            (
                "Context",
                {
                    "fields": (
                        "partner",
                        "subject_display",
                        "actor_display",
                        "business_details_display",
                        "created_at_partner_time",
                    )
                },
            ),
            (
                "Retention",
                {
                    "classes": ("collapse",),
                    "fields": ("retain_until_partner_time",),
                },
            ),
        ]
        if request.user.is_superuser:
            fieldsets.append(
                (
                    "Masked technical context",
                    {
                        "classes": ("collapse",),
                        "description": (
                            "Support-only identifiers and already-redacted payload. "
                            "Partner users do not see this section."
                        ),
                        "fields": (
                            "technical_identity_display",
                            "masked_payload_display",
                        ),
                    },
                )
            )
        return tuple(fieldsets)

    def changelist_view(self, request, extra_context=None):
        context = {"title": "Activity Log"}
        context.update(extra_context or {})
        return super().changelist_view(request, extra_context=context)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        context = {"title": "Activity details"}
        context.update(extra_context or {})
        return super().change_view(
            request,
            object_id,
            form_url=form_url,
            extra_context=context,
        )

    @admin.display(description="Activity", ordering="event_type")
    def activity_title_display(self, obj: AuditEvent) -> str:
        return activity_title(obj.event_type)

    @admin.display(description="Area", ordering="event_type")
    def category_display(self, obj: AuditEvent) -> str:
        return activity_category_label(obj.event_type)

    @admin.display(description="Summary", ordering="message")
    def summary_preview(self, obj: AuditEvent) -> str:
        message = obj.message.strip()
        if not message:
            return activity_title(obj.event_type)
        return message[:120] + ("…" if len(message) > 120 else "")

    @admin.display(description="Summary")
    def message_display(self, obj: AuditEvent) -> str:
        return obj.message.strip() or activity_title(obj.event_type)

    @admin.display(description="Reason")
    def reason_display(self, obj: AuditEvent) -> str:
        return obj.reason.strip() or "Not provided"

    @admin.display(description="Business details")
    def business_details_display(self, obj: AuditEvent):
        rows = activity_detail_rows(obj)
        if not rows:
            return "No additional details"
        return format_html_join(
            "",
            '<div style="margin-bottom: 0.35rem"><strong>{}</strong>: {}</div>',
            rows,
        )

    @admin.display(description="Subject")
    def subject_display(self, obj: AuditEvent):
        presentation = activity_target_presentation(obj)
        if presentation is None:
            return activity_category_label(obj.event_type)
        if not obj.target_id or obj.event_type.endswith(".deleted"):
            return presentation.label
        try:
            url = reverse(presentation.admin_url_name, args=[obj.target_id])
        except NoReverseMatch:
            return presentation.label
        return format_html('<a href="{}">Open {}</a>', url, presentation.label.lower())

    @admin.display(description="Performed by", ordering="actor")
    def actor_display(self, obj: AuditEvent) -> str:
        return activity_actor_label(obj)

    @admin.display(description="Occurred at (partner time)", ordering="created_at")
    def created_at_partner_time(self, obj: AuditEvent) -> str:
        return _partner_datetime(obj.created_at, obj.partner.timezone)

    @admin.display(description="Stored until (partner time)", ordering="retain_until")
    def retain_until_partner_time(self, obj: AuditEvent) -> str:
        return _partner_datetime(obj.retain_until, obj.partner.timezone)

    @admin.display(description="Technical identity")
    def technical_identity_display(self, obj: AuditEvent) -> str:
        return f"{obj.event_type} · {obj.target_type or '-'} · {obj.target_id or '-'}"

    @admin.display(description="Masked payload")
    def masked_payload_display(self, obj: AuditEvent):
        rendered = json.dumps(obj.payload_masked, ensure_ascii=False, indent=2, sort_keys=True)
        return format_html("<pre>{}</pre>", rendered)

    def get_actions(self, request):
        return {}

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PlatformDiagnostic)
class PlatformDiagnosticAdmin(admin.ModelAdmin):
    list_display = ["component", "severity", "code", "partner", "queue", "worker", "created_at"]
    list_filter = ["component", "severity", "code", "partner"]
    search_fields = ["code", "message", "queue", "worker", "target_type", "target_id"]
    autocomplete_fields = ["partner"]
    readonly_fields = [
        "metadata",
        "retain_until",
        "resolved_at",
        "created_at",
        "updated_at",
    ]

    def get_queryset(self, request):
        return platform_diagnostics_visible_to_user(request.user)

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(WorkerHeartbeat)
class WorkerHeartbeatAdmin(admin.ModelAdmin):
    list_display = [
        "component",
        "queue",
        "worker",
        "status",
        "last_seen_at",
        "last_success_at",
        "last_error_at",
        "last_error_code",
    ]
    list_filter = ["component", "status", "queue"]
    search_fields = ["component", "queue", "worker", "last_error_code"]
    readonly_fields = [
        "component",
        "queue",
        "worker",
        "status",
        "last_seen_at",
        "last_success_at",
        "last_error_at",
        "last_error_code",
        "metadata",
        "created_at",
        "updated_at",
    ]

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


def _partner_datetime(value, timezone_name: str) -> str:
    if value is None:
        return "-"
    return to_partner_time(value, timezone_name).strftime("%Y-%m-%d %H:%M:%S %Z")
