from __future__ import annotations

from dataclasses import dataclass

from apps.bot.models import BotInstance
from apps.partners.models import Partner


@dataclass(frozen=True, slots=True)
class BotRuntimeContext:
    partner: Partner
    bot_instance: BotInstance
