from __future__ import annotations

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from apps.clients.selectors import client_profiles_visible_to_user
from apps.messaging.forms import MasterReplyAdminForm
from apps.messaging.models import ConversationMessage
from apps.messaging.services import create_master_reply_to_client
from core.admin import PartnerScopedAdminMixin


@admin.register(ConversationMessage)
class ConversationMessageAdmin(PartnerScopedAdminMixin, admin.ModelAdmin):
    change_form_template = "admin/messaging/conversationmessage/change_form.html"
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
    list_select_related = ["partner", "client_profile", "created_by"]
    readonly_fields = [
        "partner",
        "client_profile",
        "direction",
        "text",
        "delivery_status",
        "failure_code",
        "failure_message_masked",
        "delivered_at",
        "created_by",
        "created_at",
        "updated_at",
    ]

    def get_urls(self):
        custom_urls = [
            path(
                "client/<uuid:client_id>/reply/",
                self.admin_site.admin_view(self.reply_view),
                name="messaging_conversationmessage_reply",
            ),
        ]
        return custom_urls + super().get_urls()

    def has_add_permission(self, request):
        """Prevent direct model creation; replies must use the owner service."""

        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def change_view(self, request, object_id, form_url="", extra_context=None):
        message = self.get_object(request, object_id)
        reply_action_url = None
        if (
            message is not None
            and message.client_profile.telegram_identity_id
            and self.has_reply_permission(request)
        ):
            reply_action_url = reverse(
                "admin:messaging_conversationmessage_reply",
                args=[message.client_profile_id],
            )
        extra_context = {
            **(extra_context or {}),
            "reply_action_url": reply_action_url,
        }
        return super().change_view(
            request,
            object_id,
            form_url=form_url,
            extra_context=extra_context,
        )

    def has_reply_permission(self, request) -> bool:
        return request.user.has_perm("messaging.add_conversationmessage")

    def reply_view(self, request, client_id):
        """Queue one tenant-scoped master reply through the messaging service."""

        if not self.has_reply_permission(request):
            raise PermissionDenied
        client_profile = client_profiles_visible_to_user(request.user).filter(id=client_id).first()
        if client_profile is None or not client_profile.telegram_identity_id:
            raise Http404("Telegram-connected client not found.")

        form = MasterReplyAdminForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                message = create_master_reply_to_client(
                    actor=request.user,
                    client_profile=client_profile,
                    text=form.cleaned_data["text"],
                )
            except ValidationError as exc:
                _add_validation_error(form, exc)
            else:
                self.log_addition(request, message, "Queued Telegram reply to client.")
                self.message_user(
                    request,
                    "Message queued for Telegram delivery.",
                    level=messages.SUCCESS,
                )
                return HttpResponseRedirect(
                    reverse(
                        "admin:messaging_conversationmessage_change",
                        args=[message.id],
                    )
                )

        context = {
            **self.admin_site.each_context(request),
            "title": "Message client",
            "subtitle": str(client_profile),
            "opts": self.model._meta,
            "client_profile": client_profile,
            "form": form,
            "media": self.media + form.media,
            "changelist_url": reverse("admin:messaging_conversationmessage_changelist"),
        }
        return TemplateResponse(
            request,
            "admin/messaging/conversationmessage/reply_form.html",
            context,
        )


def _add_validation_error(form, exc: ValidationError) -> None:
    """Map messaging service validation to the small admin reply form."""

    if hasattr(exc, "message_dict"):
        for field_name, error_messages in exc.message_dict.items():
            target = field_name if field_name in form.fields else None
            for error_message in error_messages:
                form.add_error(target, error_message)
        return
    for error_message in exc.messages:
        form.add_error(None, error_message)
