from __future__ import annotations

from apps.accounts.selectors import partner_ids_for_user


class PartnerScopedAdminMixin:
    """Filter partner-bound admin querysets through trusted memberships.

    Concrete admin adapters can reuse this once partner-owned models start
    appearing. It intentionally assumes the model has a direct `partner_id`
    field, matching the approved data-isolation rule.
    """

    partner_field_name = "partner"

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset
        return queryset.filter(
            **{
                f"{self.partner_field_name}_id__in": partner_ids_for_user(request.user),
            }
        )
