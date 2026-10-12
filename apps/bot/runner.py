from __future__ import annotations

from aiogram import Bot, Dispatcher

from apps.bot.runtime import RuntimeBotConfig


async def run_polling(configs: tuple[RuntimeBotConfig, ...]) -> None:
    """Run simple MVP polling for eligible bot configs.

    Business routers are intentionally attached in later milestones. This
    function is kept thin so management commands/runtime adapters do not own
    business behavior.
    """

    dispatcher = Dispatcher()
    bots = [Bot(token=config.token) for config in configs]
    await dispatcher.start_polling(*bots)
