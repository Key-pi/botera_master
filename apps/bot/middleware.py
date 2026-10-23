from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from apps.bot.context import BotRuntimeContext


class PartnerContextMiddleware(BaseMiddleware):
    """Inject trusted partner/bot context into aiogram handler data."""

    def __init__(self, context: BotRuntimeContext) -> None:
        self.context = context

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["partner"] = self.context.partner
        data["bot_instance"] = self.context.bot_instance
        data["bot_runtime_context"] = self.context
        return await handler(event, data)
