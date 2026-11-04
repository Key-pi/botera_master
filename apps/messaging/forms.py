from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.messaging.services import MAX_CONVERSATION_MESSAGE_LENGTH


class MasterReplyAdminForm(forms.Form):
    """Small service-backed form for a master reply through the partner bot."""

    text = forms.CharField(
        label=_("Message"),
        max_length=MAX_CONVERSATION_MESSAGE_LENGTH,
        widget=forms.Textarea(
            attrs={
                "rows": 6,
                "placeholder": _("Write a message to the client"),
            }
        ),
        help_text=_("Maximum %(count)s characters.") % {"count": MAX_CONVERSATION_MESSAGE_LENGTH},
    )
