from __future__ import annotations

from django.contrib import admin

from apps.messaging.models import ConversationMessage
from core.admin import PartnerScopedAdminMixin


@admin.register(ConversationMessage)
class ConversationMessageAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    list_display = [
        "partner",
        "client_profile",
        "direction",
        "delivery_status",
        "delivered_at",
        "created_at",
    ]
    list_filter = ["direction", "delivery_status", "partner"]
    search_fields = ["partner__name", "client_profile__name", "client_profile__phone"]
    autocomplete_fields = ["partner", "client_profile", "created_by"]
    readonly_fields = [
        "failure_code",
        "failure_message_masked",
        "delivered_at",
        "created_at",
        "updated_at",
    ]
