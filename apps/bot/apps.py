from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class BotConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.bot"
    label = "bot"
    verbose_name = _("Bot")

    def ready(self) -> None:
        from apps.bot.setup import completed_setup_steps_for_partner
        from apps.partners.setup import register_setup_checker

        register_setup_checker("bot", completed_setup_steps_for_partner)
