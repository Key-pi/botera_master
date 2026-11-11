from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.accounts.models import PartnerMembership, User
from apps.accounts.permissions import grant_partner_owner_admin_access
from apps.accounts.services import ensure_partner_access
from apps.audit.services import record_audit_event
from apps.partners.models import SUPPORTED_PARTNER_LANGUAGES, Partner
from apps.partners.setup import get_partner_setup_status
from core.timezones import DEFAULT_TIMEZONE, get_zone, normalize_timezone_name

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
    partner_name = _normalize_partner_name(partner_name)
    timezone = normalize_timezone_name(timezone)
    default_language = default_language.strip().lower()
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


def update_partner_settings(
    *,
    actor: User,
    partner: Partner,
    partner_name: str | None = None,
    timezone: str | None = None,
    default_language: str | None = None,
) -> Partner:
    """Update mutable partner defaults without exposing lifecycle status changes."""

    ensure_partner_access(actor, partner)
    normalized_name = _normalize_partner_name(partner_name) if partner_name is not None else None
    normalized_timezone = normalize_timezone_name(timezone) if timezone is not None else None
    normalized_language = default_language.strip().lower() if default_language is not None else None
    if normalized_timezone is not None or normalized_language is not None:
        _validate_partner_defaults(
            timezone=normalized_timezone if normalized_timezone is not None else partner.timezone,
            default_language=(
                normalized_language if normalized_language is not None else partner.default_language
            ),
        )

    with transaction.atomic():
        stored = Partner.objects.select_for_update().get(pk=partner.pk)
        previous = {
            "name": stored.name,
            "timezone": stored.timezone,
            "default_language": stored.default_language,
        }
        update_fields = ["updated_at"]
        if normalized_name is not None:
            stored.name = normalized_name
            update_fields.append("name")
        if normalized_timezone is not None:
            stored.timezone = normalized_timezone
            update_fields.append("timezone")
        if normalized_language is not None:
            stored.default_language = normalized_language
            update_fields.append("default_language")
        stored.save(update_fields=update_fields)
        record_audit_event(
            partner=stored,
            actor=actor,
            event_type="partner.settings_updated",
            target=stored,
            message="Partner settings updated.",
            payload={
                "previous": previous,
                "current": {
                    "name": stored.name,
                    "timezone": stored.timezone,
                    "default_language": stored.default_language,
                },
            },
        )
    return stored


def _ensure_superuser(actor: User) -> None:
    is_authenticated = getattr(actor, "is_authenticated", False)
    if not is_authenticated or not actor.is_active or not actor.is_superuser:
        raise PermissionDenied("Only a superuser can perform this action.")


def _validate_partner_defaults(*, timezone: str, default_language: str) -> None:
    try:
        get_zone(timezone)
    except Exception as exc:
        raise ValidationError({"timezone": "Unknown timezone."}) from exc
    if default_language not in SUPPORTED_PARTNER_LANGUAGES:
        raise ValidationError({"default_language": "Unsupported default language."})


def _normalize_partner_name(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValidationError({"name": "Partner name is required."})
    if len(normalized) > Partner._meta.get_field("name").max_length:
        raise ValidationError({"name": "Partner name is too long."})
    return normalized
