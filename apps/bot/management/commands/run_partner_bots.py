from __future__ import annotations

import asyncio

from django.core.management.base import BaseCommand

from apps.bot.runner import run_polling
from apps.bot.runtime import load_runtime_bot_configs


class Command(BaseCommand):
    help = "Run polling for eligible active partner bots."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Load runtime configs and report skipped bots without starting polling.",
        )

    def handle(self, *args, **options) -> None:
        result = load_runtime_bot_configs()
        self.stdout.write(f"Loaded {len(result.configs)} eligible bot(s).")
        for skipped in result.skipped:
            self.stdout.write(
                self.style.WARNING(
                    f"Skipped bot={skipped.bot_instance_id} partner={skipped.partner_id} "
                    f"reason={skipped.reason_code}"
                )
            )

        if options["dry_run"]:
            return
        if not result.configs:
            self.stdout.write(self.style.WARNING("No eligible bots to run."))
            return

        asyncio.run(run_polling(result.configs))
