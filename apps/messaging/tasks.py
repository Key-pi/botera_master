from __future__ import annotations

from celery import shared_task

from apps.bot.delivery import deliver_conversation_message
from apps.messaging.models import ConversationMessage
from apps.messaging.selectors import queued_conversation_messages
from apps.messaging.services import dispatch_conversation_message


@shared_task(name="messaging.dispatch_queued_conversation_messages", queue="notifications")
def dispatch_queued_conversation_messages_task(limit: int = 100) -> int:
    """Dispatch queued bot-mediated client/master messages through Telegram."""

    message_ids = list(queued_conversation_messages().values_list("id", flat=True)[:limit])
    processed = 0
    for message_id in message_ids:
        message = ConversationMessage.objects.get(id=message_id)
        dispatch_conversation_message(message=message, deliver=deliver_conversation_message)
        processed += 1
    return processed
