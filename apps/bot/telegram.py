from __future__ import annotations

from dataclasses import dataclass

from aiogram import Bot


@dataclass(frozen=True, slots=True)
class TelegramBotIdentity:
    telegram_bot_id: int
    username: str
    first_name: str


async def fetch_bot_identity(raw_token: str) -> TelegramBotIdentity:
    """Fetch Telegram bot identity for explicit connection checks."""

    bot = Bot(token=raw_token)
    try:
        me = await bot.get_me()
    finally:
        await bot.session.close()

    return TelegramBotIdentity(
        telegram_bot_id=me.id,
        username=me.username or "",
        first_name=me.first_name or "",
    )
