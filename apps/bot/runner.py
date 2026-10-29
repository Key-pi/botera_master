from __future__ import annotations

import asyncio
from contextlib import suppress

from aiogram import Bot, Dispatcher
from asgiref.sync import sync_to_async

from apps.audit.models import PlatformDiagnostic
from apps.audit.services import record_worker_heartbeat
from apps.bot.context import BotRuntimeContext
from apps.bot.middleware import PartnerContextMiddleware
from apps.bot.models import BotInstance
from apps.bot.routers import build_partner_router
from apps.bot.runtime import RuntimeBotConfig

BOT_RUNNER_HEARTBEAT_INTERVAL_SECONDS = 60


async def run_polling(configs: tuple[RuntimeBotConfig, ...]) -> None:
    """Run simple MVP polling for eligible bot configs.

    Each bot gets its own dispatcher so partner context is attached from trusted
    runtime config, not from callback payloads or visible Telegram data.
    """

    heartbeat_task = asyncio.create_task(_record_bot_runner_heartbeat_loop())
    try:
        await asyncio.gather(*(_run_single_bot(config) for config in configs))
    finally:
        heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat_task


async def _record_bot_runner_heartbeat_loop() -> None:
    """Keep one durable current signal for the polling runner process."""

    while True:
        await sync_to_async(record_worker_heartbeat, thread_sensitive=True)(
            component=PlatformDiagnostic.Component.BOT_RUNNER,
        )
        await asyncio.sleep(BOT_RUNNER_HEARTBEAT_INTERVAL_SECONDS)


async def _run_single_bot(config: RuntimeBotConfig) -> None:
    bot_instance = await sync_to_async(
        lambda: BotInstance.objects.select_related("partner").get(id=config.bot_instance_id)
    )()
    context = BotRuntimeContext(
        partner=bot_instance.partner,
        bot_instance=bot_instance,
    )
    dispatcher = Dispatcher()
    partner_context = PartnerContextMiddleware(context)
    dispatcher.message.middleware(partner_context)
    dispatcher.callback_query.middleware(partner_context)
    dispatcher.include_router(build_partner_router())
    bot = Bot(token=config.token)
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()
