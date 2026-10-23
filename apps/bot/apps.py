from django.apps import AppConfig


class BotConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.bot"
    label = "bot"

    def ready(self) -> None:
        from apps.bot.setup import completed_setup_steps_for_partner
        from apps.partners.setup import register_setup_checker

        register_setup_checker("bot", completed_setup_steps_for_partner)
