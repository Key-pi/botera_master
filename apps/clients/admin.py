from __future__ import annotations

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator

from apps.appointments.selectors import (
    appointment_history_for_client,
    future_appointments_for_client,
)
from apps.clients.forms import ClientProfileAdminForm
from apps.clients.models import ClientProfile, TelegramIdentity
from apps.clients.selectors import client_operational_summary
from apps.clients.services import update_client_profile
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
    form = ClientProfileAdminForm
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
    list_select_related = ["partner", "telegram_identity"]
    fieldsets = (
        (
            "Client data",
            {"fields": ("partner", "telegram_identity", "name", "phone", "language")},
        ),
        (
            "Partner notes",
            {"fields": ("private_master_note", "extra_client_text")},
        ),
        (
            "Communication state",
            {
                "classes": ("collapse",),
                "fields": (
                    "marketing_opt_out",
                    "bot_unreachable",
                    "last_delivery_failure_code",
                    "last_delivery_failure_at",
                ),
            },
        ),
        (
            "System",
            {
                "classes": ("collapse",),
                "fields": ("created_at", "updated_at"),
            },
        ),
    )
    readonly_fields = [
        "partner",
        "telegram_identity",
        "marketing_opt_out",
        "bot_unreachable",
        "last_delivery_failure_code",
        "last_delivery_failure_at",
        "created_at",
        "updated_at",
    ]

    @admin.display(description="Phone")
    def masked_phone(self, obj: ClientProfile) -> str:
        return mask_phone(obj.phone) if obj.phone else ""

    def save_model(self, request, obj, form, change):
        if not change:
            raise PermissionDenied("Client profiles must be created through an owner service.")
        stored = update_client_profile(
            actor=request.user,
            client_profile=obj,
            name=form.cleaned_data["name"],
            phone=form.cleaned_data["phone"],
            language=form.cleaned_data["language"],
            private_master_note=form.cleaned_data["private_master_note"],
            extra_client_text=form.cleaned_data["extra_client_text"],
        )
        _copy_model_state(target=obj, source=stored)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

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


def _copy_model_state(*, target: ClientProfile, source: ClientProfile) -> None:
    """Keep the admin form instance aligned with the service-updated row."""

    for field in source._meta.concrete_fields:
        setattr(target, field.attname, getattr(source, field.attname))
    target._state.adding = False
    target._state.db = source._state.db
