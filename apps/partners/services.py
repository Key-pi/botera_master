from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.accounts.models import PartnerMembership, User
from apps.accounts.permissions import grant_partner_owner_admin_access
from apps.accounts.services import ensure_partner_access
from apps.partners.models import Partner
from apps.partners.setup import get_partner_setup_status
from core.timezones import DEFAULT_TIMEZONE, get_zone

DEFAULT_LANGUAGE = "uk"


@dataclass(frozen=True)
class CreatedPartnerOwner:
    partner: Partner
    owner: User
    membership: PartnerMembership


def create_partner_with_owner(
    *,
    actor: User,
    partner_name: str,
    owner_username: str,
    owner_password: str,
    owner_email: str = "",
    owner_first_name: str = "",
    owner_last_name: str = "",
    timezone: str = DEFAULT_TIMEZONE,
    default_language: str = DEFAULT_LANGUAGE,
) -> CreatedPartnerOwner:
    """Create a setup-required partner and its MVP owner/master account."""

    _ensure_superuser(actor)
    _validate_partner_defaults(timezone=timezone, default_language=default_language)

    user_model = get_user_model()
    with transaction.atomic():
        partner = Partner.objects.create(
            name=partner_name,
            status=Partner.Status.SETUP_REQUIRED,
            timezone=timezone,
            default_language=default_language,
        )
        owner = user_model.objects.create_user(
            username=owner_username,
            email=owner_email,
            password=owner_password,
            first_name=owner_first_name,
            last_name=owner_last_name,
            is_staff=True,
        )
        membership = PartnerMembership.objects.create(
            user=owner,
            partner=partner,
            role=PartnerMembership.Role.OWNER,
        )
        grant_partner_owner_admin_access(owner)
        from apps.schedule.services import initialize_partner_schedule

        initialize_partner_schedule(partner=partner)
        from apps.notifications.services import (
            ensure_default_reminder_rules,
            get_or_create_notification_settings,
        )

        get_or_create_notification_settings(partner=partner)
        ensure_default_reminder_rules(partner=partner)
        from apps.finance.services import get_or_create_penalty_settings

        get_or_create_penalty_settings(partner=partner)

    return CreatedPartnerOwner(partner=partner, owner=owner, membership=membership)


def activate_partner_after_setup(
    *,
    actor: User,
    partner: Partner,
    completed_external_steps: set[str] | None = None,
) -> Partner:
    """Move a partner to active only when minimum launch setup is complete."""

    ensure_partner_access(actor, partner)
    if partner.status == Partner.Status.SUSPENDED and not actor.is_superuser:
        raise PermissionDenied("Only a superuser can reactivate a suspended partner.")
    if partner.status == Partner.Status.ARCHIVED:
        raise ValidationError("Archived partners cannot be activated.")

    setup_status = get_partner_setup_status(
        partner,
        completed_external_steps=completed_external_steps,
    )
    if not setup_status.is_complete:
        raise ValidationError({"setup": list(setup_status.missing_steps)})

    partner.status = Partner.Status.ACTIVE
    partner.save(update_fields=["status", "updated_at"])
    return partner


def _ensure_superuser(actor: User) -> None:
    is_authenticated = getattr(actor, "is_authenticated", False)
    if not is_authenticated or not actor.is_active or not actor.is_superuser:
        raise PermissionDenied("Only a superuser can perform this action.")


def _validate_partner_defaults(*, timezone: str, default_language: str) -> None:
    try:
        get_zone(timezone)
    except Exception as exc:
        raise ValidationError({"timezone": "Unknown timezone."}) from exc
    if not default_language:
        raise ValidationError({"default_language": "Default language is required."})
