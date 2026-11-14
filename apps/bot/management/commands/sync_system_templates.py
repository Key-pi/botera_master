from django.core.management.base import BaseCommand

from apps.bot.services import seed_system_templates


class Command(BaseCommand):
    help = "Synchronize code-owned Telegram system templates into the database."

    def handle(self, *args, **options) -> None:
        synchronized_count = seed_system_templates()
        self.stdout.write(
            self.style.SUCCESS(
                f"Synchronized {synchronized_count} system template rows."
            )
        )
