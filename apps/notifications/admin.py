from __future__ import annotations

from django.contrib import admin

from apps.notifications.models import (
    DeliveryAttempt,
    PartnerNotificationSettings,
    ReminderRule,
    ScheduledNotification,
)
from core.admin import PartnerScopedAdminMixin


@admin.register(PartnerNotificationSettings)
class PartnerNotificationSettingsAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = [
        "partner",
        "quiet_hours_enabled",
        "quiet_hours_start_local",
        "quiet_hours_end_local",
        "morning_digest_enabled",
        "morning_digest_time_local",
        "stale_requested_base_interval_minutes",
    ]
    search_fields = ["partner__name"]
    autocomplete_fields = ["partner"]


@admin.register(ReminderRule)
class ReminderRuleAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = ["partner", "target", "offset_minutes", "is_active"]
    list_filter = ["target", "is_active", "partner"]
    search_fields = ["partner__name"]
    autocomplete_fields = ["partner"]


@admin.register(ScheduledNotification)
class ScheduledNotificationAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = [
        "partner",
        "type",
        "status",
        "due_at",
        "attempt_count",
        "failed_visible_to_partner",
    ]
    list_filter = ["type", "status", "failed_visible_to_partner", "partner"]
    search_fields = ["idempotency_key", "partner__name", "last_failure_code"]
    autocomplete_fields = [
        "partner",
        "appointment",
        "appointment_proposal",
        "client_profile",
        "reminder_rule",
    ]
    readonly_fields = [
        "idempotency_key",
        "source_notification",
        "attempt_count",
        "last_failure_code",
        "last_failure_message_masked",
        "sent_at",
        "created_at",
        "updated_at",
    ]


@admin.register(DeliveryAttempt)
class DeliveryAttemptAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = [
        "partner",
        "notification",
        "broadcast_recipient",
        "conversation_message",
        "channel",
        "status",
        "attempted_at",
    ]
    list_filter = ["channel", "status", "partner"]
    search_fields = [
        "notification__idempotency_key",
        "broadcast_recipient__campaign__text",
        "conversation_message__text",
        "failure_code",
    ]
    autocomplete_fields = [
        "partner",
        "notification",
        "broadcast_recipient",
        "conversation_message",
    ]
    readonly_fields = [
        "failure_code",
        "failure_message_masked",
        "message_preview_masked",
        "attempted_at",
        "created_at",
        "updated_at",
    ]
