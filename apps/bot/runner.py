from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from asgiref.sync import sync_to_async

from apps.bot.context import BotRuntimeContext
from apps.bot.middleware import PartnerContextMiddleware
from apps.bot.models import BotInstance
from apps.bot.routers import build_partner_router
from apps.bot.runtime import RuntimeBotConfig


async def run_polling(configs: tuple[RuntimeBotConfig, ...]) -> None:
    """Run simple MVP polling for eligible bot configs.

    Each bot gets its own dispatcher so partner context is attached from trusted
    runtime config, not from callback payloads or visible Telegram data.
    """

    await asyncio.gather(*(_run_single_bot(config) for config in configs))


async def _run_single_bot(config: RuntimeBotConfig) -> None:
    bot_instance = await sync_to_async(
        lambda: BotInstance.objects.select_related("partner").get(id=config.bot_instance_id)
    )()
    context = BotRuntimeContext(
        partner=bot_instance.partner,
        bot_instance=bot_instance,
    )
    dispatcher = Dispatcher()
    dispatcher.message.middleware(PartnerContextMiddleware(context))
    dispatcher.include_router(build_partner_router())
    bot = Bot(token=config.token)
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()
