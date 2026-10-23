from __future__ import annotations

from django import forms

from apps.messaging.services import MAX_CONVERSATION_MESSAGE_LENGTH


class MasterReplyAdminForm(forms.Form):
    """Small service-backed form for a master reply through the partner bot."""

    text = forms.CharField(
        label="Message",
        max_length=MAX_CONVERSATION_MESSAGE_LENGTH,
        widget=forms.Textarea(
            attrs={
                "rows": 6,
                "placeholder": "Write a message to the client",
            }
        ),
        help_text=f"Maximum {MAX_CONVERSATION_MESSAGE_LENGTH} characters.",
    )
