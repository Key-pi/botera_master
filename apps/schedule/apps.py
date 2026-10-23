from django.apps import AppConfig


class ScheduleConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.schedule"
    label = "schedule"

    def ready(self) -> None:
        from apps.partners.setup import register_setup_checker
        from apps.schedule.setup import completed_setup_steps_for_partner

        register_setup_checker("schedule", completed_setup_steps_for_partner)
