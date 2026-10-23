from __future__ import annotations

from django.contrib import admin

from apps.broadcasts.models import BroadcastCampaign, BroadcastRecipient, BroadcastTemplate
from core.admin import PartnerScopedAdminMixin


@admin.register(BroadcastCampaign)
class BroadcastCampaignAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = [
        "partner",
        "status",
        "queued_count",
        "sent_count",
        "failed_count",
        "skipped_count",
        "scheduled_at",
        "created_at",
    ]
    list_filter = ["status", "partner"]
    search_fields = ["partner__name", "text"]
    autocomplete_fields = ["partner", "created_by"]
    readonly_fields = [
        "queued_count",
        "sent_count",
        "failed_count",
        "skipped_count",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
    ]


@admin.register(BroadcastRecipient)
class BroadcastRecipientAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = [
        "partner",
        "campaign",
        "client_profile",
        "status",
        "attempt_count",
        "next_attempt_at",
        "sent_at",
    ]
    list_filter = ["status", "partner"]
    search_fields = ["campaign__text", "client_profile__name", "client_profile__phone"]
    autocomplete_fields = ["partner", "campaign", "client_profile"]
    readonly_fields = [
        "attempt_count",
        "failure_code",
        "failure_message_masked",
        "sent_at",
        "created_at",
        "updated_at",
    ]


@admin.register(BroadcastTemplate)
class BroadcastTemplateAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = ["partner", "name", "created_by", "created_at"]
    list_filter = ["partner"]
    search_fields = ["partner__name", "name", "text"]
    autocomplete_fields = ["partner", "created_by"]
    readonly_fields = ["created_at", "updated_at"]
