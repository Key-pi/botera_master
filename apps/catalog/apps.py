from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.catalog"
    label = "catalog"
    verbose_name = _("Catalog")

    def ready(self) -> None:
        from apps.catalog.setup import completed_setup_steps_for_partner
        from apps.partners.setup import register_setup_checker

        register_setup_checker("catalog", completed_setup_steps_for_partner)
