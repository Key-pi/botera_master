from __future__ import annotations

from django.core.exceptions import PermissionDenied

from apps.accounts.selectors import partner_ids_for_user, user_can_access_partner


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

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == self.partner_field_name and not request.user.is_superuser:
            kwargs["queryset"] = db_field.remote_field.model.objects.filter(
                id__in=partner_ids_for_user(request.user)
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        partner = getattr(obj, self.partner_field_name)
        if not request.user.is_superuser and not user_can_access_partner(request.user, partner):
            raise PermissionDenied("Object belongs to another partner.")
        return super().save_model(request, obj, form, change)
