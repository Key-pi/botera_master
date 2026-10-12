from django.apps import AppConfig


class CatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.catalog"
    label = "catalog"

    def ready(self) -> None:
        from apps.catalog.setup import completed_setup_steps_for_partner
        from apps.partners.setup import register_setup_checker

        register_setup_checker("catalog", completed_setup_steps_for_partner)
