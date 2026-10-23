from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.services import ensure_partner_access
from apps.clients.models import ClientProfile, TelegramIdentity
from apps.clients.phone import normalize_phone

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.partners.models import Partner


SUPPORTED_CLIENT_LANGUAGES = {"uk", "ru", "en"}


@dataclass(frozen=True, slots=True)
class ClientProfileDetails:
    """Normalized partner-editable client profile values."""

    name: str
    phone: str
    language: str
    private_master_note: str
    extra_client_text: str


def upsert_telegram_identity(
    *,
    telegram_user_id: int,
    username: str = "",
    first_name: str = "",
    last_name: str = "",
    is_premium: bool = False,
    language_code: str = "",
    seen_at=None,
) -> TelegramIdentity:
    """Create or refresh the global technical identity for a Telegram user."""

    seen_at = seen_at or timezone.now()
    identity, _created = TelegramIdentity.objects.update_or_create(
        telegram_user_id=telegram_user_id,
        defaults={
            "username": username or "",
            "first_name": first_name or "",
            "last_name": last_name or "",
            "is_premium": bool(is_premium),
            "language_code": language_code or "",
            "last_seen_at": seen_at,
        },
    )
    return identity


def resolve_client_profile_for_telegram_identity(
    *,
    partner: Partner,
    telegram_identity: TelegramIdentity,
) -> ClientProfile:
    """Return the partner-scoped profile for incoming Telegram activity.

    Incoming activity proves the bot channel works again for this partner, so
    delivery-unreachable markers are cleared here rather than on the shared
    Telegram identity.
    """

    default_name = _display_name_from_identity(telegram_identity)
    default_language = _language_for_partner_profile(partner, telegram_identity.language_code)
    with transaction.atomic():
        profile, created = ClientProfile.objects.select_for_update().get_or_create(
            partner=partner,
            telegram_identity=telegram_identity,
            defaults={
                "name": default_name,
                "language": default_language,
            },
        )
        update_fields: list[str] = []
        if not created:
            if not profile.name and default_name:
                profile.name = default_name
                update_fields.append("name")
            if not profile.language:
                profile.language = default_language
                update_fields.append("language")
        if profile.bot_unreachable:
            profile.bot_unreachable = False
            profile.last_delivery_failure_code = ""
            profile.last_delivery_failure_at = None
            update_fields.extend(
                [
                    "bot_unreachable",
                    "last_delivery_failure_code",
                    "last_delivery_failure_at",
                ]
            )
        if update_fields:
            update_fields.append("updated_at")
            profile.save(update_fields=update_fields)
    return profile


def create_manual_client_profile(
    *,
    actor: User,
    partner: Partner,
    name: str = "",
    phone: str = "",
    language: str | None = None,
    private_master_note: str = "",
    extra_client_text: str = "",
) -> ClientProfile:
    """Create a partner-scoped manual client without Telegram linkage."""

    ensure_partner_access(actor, partner)
    details = validate_client_profile_details(
        partner=partner,
        has_telegram_identity=False,
        name=name,
        phone=phone,
        language=language,
        private_master_note=private_master_note,
        extra_client_text=extra_client_text,
    )
    return ClientProfile.objects.create(
        partner=partner,
        name=details.name,
        phone=details.phone,
        language=details.language,
        private_master_note=details.private_master_note,
        extra_client_text=details.extra_client_text,
    )


def update_client_profile(
    *,
    actor: User,
    client_profile: ClientProfile,
    name: str,
    phone: str,
    language: str,
    private_master_note: str = "",
    extra_client_text: str = "",
) -> ClientProfile:
    """Update only partner-editable client data under a row lock."""

    with transaction.atomic():
        profile = (
            ClientProfile.objects.select_for_update()
            .select_related("partner", "telegram_identity")
            .get(id=client_profile.id)
        )
        ensure_partner_access(actor, profile.partner)
        details = validate_client_profile_details(
            partner=profile.partner,
            has_telegram_identity=profile.has_telegram_identity,
            name=name,
            phone=phone,
            language=language,
            private_master_note=private_master_note,
            extra_client_text=extra_client_text,
        )
        profile.name = details.name
        profile.phone = details.phone
        profile.language = details.language
        profile.private_master_note = details.private_master_note
        profile.extra_client_text = details.extra_client_text
        profile.save(
            update_fields=[
                "name",
                "phone",
                "language",
                "private_master_note",
                "extra_client_text",
                "updated_at",
            ]
        )
        return profile


def validate_client_profile_details(
    *,
    partner: Partner,
    has_telegram_identity: bool,
    name: str,
    phone: str,
    language: str | None,
    private_master_note: str = "",
    extra_client_text: str = "",
) -> ClientProfileDetails:
    """Normalize and validate values shared by admin forms and write services."""

    cleaned_name = name.strip()
    raw_phone = (phone or "").strip()
    normalized_phone = normalize_phone(raw_phone)
    if raw_phone and not normalized_phone:
        raise ValidationError({"phone": "Phone must contain at least one digit."})
    if not has_telegram_identity and not cleaned_name and not normalized_phone:
        raise ValidationError({"name": "Manual client needs at least a name or phone."})

    return ClientProfileDetails(
        name=cleaned_name,
        phone=normalized_phone,
        language=_language_or_default(partner, language),
        private_master_note=private_master_note.strip(),
        extra_client_text=extra_client_text.strip(),
    )


def link_client_profile_to_telegram_identity(
    *,
    actor: User,
    client_profile: ClientProfile,
    telegram_identity: TelegramIdentity,
) -> ClientProfile:
    """Attach a manual client profile to a Telegram identity after explicit approval."""

    ensure_partner_access(actor, client_profile.partner)
    with transaction.atomic():
        profile = ClientProfile.objects.select_for_update().get(id=client_profile.id)
        if profile.telegram_identity_id and profile.telegram_identity_id != telegram_identity.id:
            raise ValidationError({"telegram_identity": "Client profile is already linked."})

        existing = (
            ClientProfile.objects.filter(
                partner=profile.partner,
                telegram_identity=telegram_identity,
            )
            .exclude(id=profile.id)
            .exists()
        )
        if existing:
            raise ValidationError(
                {"telegram_identity": "This Telegram identity already has a client profile."}
            )

        update_fields = ["telegram_identity", "bot_unreachable", "updated_at"]
        profile.telegram_identity = telegram_identity
        profile.bot_unreachable = False
        if profile.last_delivery_failure_code or profile.last_delivery_failure_at:
            profile.last_delivery_failure_code = ""
            profile.last_delivery_failure_at = None
            update_fields.extend(["last_delivery_failure_code", "last_delivery_failure_at"])
        profile.save(update_fields=update_fields)
        return profile


def set_marketing_opt_out(*, client_profile: ClientProfile, opt_out: bool) -> ClientProfile:
    """Update marketing subscription without affecting service notifications."""

    client_profile.marketing_opt_out = bool(opt_out)
    client_profile.save(update_fields=["marketing_opt_out", "updated_at"])
    return client_profile


def update_client_profile_language(
    *,
    client_profile: ClientProfile,
    language: str,
) -> ClientProfile:
    """Set the client's preferred bot language in partner scope."""

    cleaned_language = language.strip().lower()
    if cleaned_language not in SUPPORTED_CLIENT_LANGUAGES:
        raise ValidationError({"language": "Unsupported client language."})
    client_profile.language = cleaned_language
    client_profile.save(update_fields=["language", "updated_at"])
    return client_profile


def update_client_profile_phone_from_telegram(
    *,
    client_profile: ClientProfile,
    phone: str,
) -> ClientProfile:
    """Store a phone supplied by the client in this partner bot.

    The caller must resolve ``client_profile`` from trusted partner/runtime
    context first. This narrow service deliberately cannot edit any private
    master-owned profile data.
    """

    raw_phone = (phone or "").strip()
    normalized_phone = normalize_phone(raw_phone)
    if not raw_phone or not normalized_phone:
        raise ValidationError({"phone": "Phone must contain at least one digit."})

    with transaction.atomic():
        profile = ClientProfile.objects.select_for_update().get(id=client_profile.id)
        profile.phone = normalized_phone
        profile.save(update_fields=["phone", "updated_at"])
        return profile


def mark_client_unreachable(
    *,
    client_profile: ClientProfile,
    failure_code: str,
) -> ClientProfile:
    """Persist a terminal bot-delivery failure for this partner/client only."""

    client_profile.bot_unreachable = True
    client_profile.last_delivery_failure_code = failure_code[:128]
    client_profile.last_delivery_failure_at = timezone.now()
    client_profile.save(
        update_fields=[
            "bot_unreachable",
            "last_delivery_failure_code",
            "last_delivery_failure_at",
            "updated_at",
        ]
    )
    return client_profile


def mark_client_reachable(*, client_profile: ClientProfile) -> ClientProfile:
    """Clear partner-scoped bot reachability failure state."""

    client_profile.bot_unreachable = False
    client_profile.last_delivery_failure_code = ""
    client_profile.last_delivery_failure_at = None
    client_profile.save(
        update_fields=[
            "bot_unreachable",
            "last_delivery_failure_code",
            "last_delivery_failure_at",
            "updated_at",
        ]
    )
    return client_profile


def _display_name_from_identity(identity: TelegramIdentity) -> str:
    name = " ".join(part for part in [identity.first_name, identity.last_name] if part).strip()
    return name or identity.username


def _language_for_partner_profile(partner: Partner, language_code: str) -> str:
    if language_code in SUPPORTED_CLIENT_LANGUAGES:
        return language_code
    return partner.default_language


def _language_or_default(partner: Partner, language: str | None) -> str:
    if language is None or not language.strip():
        return partner.default_language
    normalized_language = language.strip().lower()
    if normalized_language not in SUPPORTED_CLIENT_LANGUAGES:
        raise ValidationError({"language": "Unsupported client language."})
    return normalized_language
