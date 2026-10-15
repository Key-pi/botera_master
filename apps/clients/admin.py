from __future__ import annotations

from django.contrib import admin
from django.core.paginator import Paginator

from apps.appointments.selectors import (
    appointment_history_for_client,
    future_appointments_for_client,
)
from apps.clients.models import ClientProfile, TelegramIdentity
from apps.clients.selectors import client_operational_summary
from apps.finance.selectors import outstanding_penalties_for_client
from core.admin import PartnerScopedAdminMixin
from core.security.masking import mask_phone

CLIENT_CARD_PAGE_SIZE = 5


@admin.register(TelegramIdentity)
class TelegramIdentityAdmin(admin.ModelAdmin):
    list_display = [
        "telegram_user_id",
        "username",
        "first_name",
        "last_name",
        "is_premium",
        "language_code",
        "last_seen_at",
    ]
    search_fields = ["=telegram_user_id", "username", "first_name", "last_name"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(ClientProfile)
class ClientProfileAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    change_form_template = "admin/clients/clientprofile/change_form.html"
    list_display = [
        "display_name",
        "partner",
        "masked_phone",
        "language",
        "telegram_identity",
        "marketing_opt_out",
        "bot_unreachable",
    ]
    list_filter = ["partner", "language", "marketing_opt_out", "bot_unreachable"]
    search_fields = ["name", "phone", "telegram_identity__username"]
    autocomplete_fields = ["partner", "telegram_identity"]
    readonly_fields = ["created_at", "updated_at"]

    @admin.display(description="Phone")
    def masked_phone(self, obj: ClientProfile) -> str:
        return mask_phone(obj.phone) if obj.phone else ""

    def change_view(self, request, object_id, form_url="", extra_context=None):
        """Add tenant-scoped operational data to the normal client detail view."""

        client_profile = self.get_object(request, object_id)
        if client_profile is not None:
            future_page = Paginator(
                future_appointments_for_client(client_profile=client_profile),
                CLIENT_CARD_PAGE_SIZE,
            ).get_page(request.GET.get("future_page"))
            history_page = Paginator(
                appointment_history_for_client(client_profile=client_profile),
                CLIENT_CARD_PAGE_SIZE,
            ).get_page(request.GET.get("history_page"))
            penalty_page = Paginator(
                outstanding_penalties_for_client(client_profile),
                CLIENT_CARD_PAGE_SIZE,
            ).get_page(request.GET.get("penalty_page"))
            extra_context = {
                **(extra_context or {}),
                "client_summary": client_operational_summary(
                    client_profile=client_profile,
                ),
                "future_appointments_page": future_page,
                "appointment_history_page": history_page,
                "outstanding_penalties_page": penalty_page,
            }
        return super().change_view(
            request,
            object_id,
            form_url=form_url,
            extra_context=extra_context,
        )
