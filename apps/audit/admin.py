from __future__ import annotations

from django.contrib import admin

from apps.audit.models import AuditEvent, PlatformDiagnostic, WorkerHeartbeat
from apps.audit.selectors import audit_events_visible_to_user, platform_diagnostics_visible_to_user
from core.admin import PartnerScopedAdminMixin


@admin.register(AuditEvent)
class AuditEventAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = ["partner", "event_type", "target_type", "target_id", "actor", "created_at"]
    list_filter = ["event_type", "partner"]
    search_fields = ["partner__name", "event_type", "target_type", "target_id", "message"]
    autocomplete_fields = ["partner", "actor"]
    readonly_fields = [
        "payload_masked",
        "retain_until",
        "created_at",
        "updated_at",
    ]

    def get_queryset(self, request):
        return audit_events_visible_to_user(request.user)


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
