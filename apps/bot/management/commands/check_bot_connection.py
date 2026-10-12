from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.bot.models import BotInstance
from apps.bot.services import check_bot_connection


class Command(BaseCommand):
    help = "Check one partner bot connection through Telegram getMe."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--partner-id", required=True, help="Partner UUID.")

    def handle(self, *args, **options) -> None:
        try:
            bot_instance = BotInstance.objects.select_related("partner").get(
                partner_id=options["partner_id"],
            )
        except BotInstance.DoesNotExist as exc:
            raise CommandError("Bot instance does not exist for this partner.") from exc

        check_bot_connection(bot_instance)
        bot_instance.refresh_from_db()
        if bot_instance.last_error_code:
            raise CommandError(
                f"Connection check failed: {bot_instance.last_error_code}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"OK @{bot_instance.telegram_username} "
                f"({bot_instance.telegram_bot_id}) for {bot_instance.partner}"
            )
        )
