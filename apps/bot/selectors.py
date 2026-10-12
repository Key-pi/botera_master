from __future__ import annotations

from django.db.models import QuerySet

from apps.bot.models import BotInstance


def active_polling_bot_instances() -> QuerySet[BotInstance]:
    """Return bot records that are operationally active before setup/token checks."""

    return BotInstance.objects.select_related("partner").filter(
        status=BotInstance.Status.ACTIVE,
        delivery_mode=BotInstance.DeliveryMode.POLLING,
    )
