from __future__ import annotations

from apps.catalog.selectors import active_services_for_booking
from apps.partners.models import Partner
from apps.partners.setup import SetupStep


def completed_setup_steps_for_partner(partner: Partner) -> set[str]:
    """Report whether the partner has at least one bookable service."""

    if active_services_for_booking(partner).exists():
        return {SetupStep.CATALOG_SERVICE}
    return set()
