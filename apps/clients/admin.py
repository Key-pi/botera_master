from __future__ import annotations

from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from apps.appointments.selectors import (
    appointment_history_for_client,
    future_appointments_for_client,
)
from apps.clients.forms import ClientProfileAdminForm
from apps.clients.models import ClientProfile, TelegramIdentity
from apps.clients.selectors import client_operational_summary
from apps.clients.services import update_client_profile
from apps.finance.forms import ClientPenaltyActionAdminForm, add_domain_errors_to_form
from apps.finance.selectors import outstanding_penalties_for_client
from apps.finance.services import mark_penalty_paid, waive_penalty
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

    def get_urls(self):
        custom_urls = [
            path(
                "<path:object_id>/penalties/<uuid:penalty_id>/action/",
                self.admin_site.admin_view(self.penalty_action_view),
                name="clients_clientprofile_penalty_action",
            ),
        ]
        return custom_urls + super().get_urls()

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
                "message_client_url": (
                    reverse(
                        "admin:messaging_conversationmessage_reply",
                        args=[client_profile.id],
                    )
                    if client_profile.telegram_identity_id
                    and request.user.has_perm("messaging.add_conversationmessage")
                    else None
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

    def penalty_action_view(self, request, object_id, penalty_id):
        """Run one explicit debt action through the finance owner service."""

        client_profile = self.get_object(request, object_id)
        if client_profile is None:
            raise Http404("Client not found.")
        if not self.has_change_permission(request, client_profile):
            raise PermissionDenied

        penalty = outstanding_penalties_for_client(client_profile).filter(id=penalty_id).first()
        if penalty is None:
            raise Http404("Outstanding penalty not found.")

        form = ClientPenaltyActionAdminForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            action = form.cleaned_data["action"]
            try:
                if action == ClientPenaltyActionAdminForm.Action.MARK_PAID:
                    stored = mark_penalty_paid(actor=request.user, penalty=penalty)
                    action_label = "marked as paid"
                else:
                    stored = waive_penalty(
                        actor=request.user,
                        penalty=penalty,
                        note=form.cleaned_data["note"],
                    )
                    action_label = "waived"
            except ValidationError as exc:
                add_domain_errors_to_form(form, exc)
            else:
                self.log_change(
                    request,
                    client_profile,
                    f"Penalty {stored.id} {action_label}.",
                )
                self.message_user(request, f"Penalty {action_label}.")
                return HttpResponseRedirect(
                    f"{reverse('admin:clients_clientprofile_change', args=[client_profile.id])}"
                    "#outstanding-penalties"
                )

        context = {
            **self.admin_site.each_context(request),
            "title": "Manage penalty",
            "subtitle": str(client_profile),
            "opts": self.model._meta,
            "client_profile": client_profile,
            "penalty": penalty,
            "form": form,
            "change_url": reverse(
                "admin:clients_clientprofile_change",
                args=[client_profile.id],
            ),
        }
        return TemplateResponse(
            request,
            "admin/clients/clientprofile/penalty_action_form.html",
            context,
        )


def _copy_model_state(*, target: ClientProfile, source: ClientProfile) -> None:
    """Keep the admin form instance aligned with the service-updated row."""

    for field in source._meta.concrete_fields:
        setattr(target, field.attname, getattr(source, field.attname))
    target._state.adding = False
    target._state.db = source._state.db
