from __future__ import annotations

from apps.bot.models import BotInstance
from apps.partners.models import Partner
from apps.partners.setup import SetupStep


def completed_setup_steps_for_partner(partner: Partner) -> set[str]:
    """Report bot-owned setup facts to partner onboarding checks."""

    if BotInstance.objects.filter(
        partner=partner,
        encrypted_token__gt="",
        token_fingerprint__isnull=False,
    ).exists():
        return {SetupStep.BOT_TOKEN}
    return set()
