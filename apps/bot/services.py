from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from string import Formatter

from asgiref.sync import async_to_sync
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from apps.accounts.models import User
from apps.accounts.services import ensure_partner_access
from apps.bot.content import SYSTEM_TEMPLATE_DEFINITION_BY_KEY, SYSTEM_TEMPLATE_DEFINITIONS
from apps.bot.models import (
    DEFAULT_TEMPLATE_LANGUAGE,
    SUPPORTED_TEMPLATE_LANGUAGES,
    BotInstance,
    BotTemplateContentType,
    PartnerTelegramContact,
    PartnerTelegramContactLink,
    PartnerTemplateOverride,
    SystemTemplate,
)
from apps.bot.telegram import fetch_bot_identity
from apps.bot.token_validation import validate_bot_token_format
from apps.clients.models import TelegramIdentity
from apps.clients.services import (
    resolve_client_profile_for_telegram_identity,
    upsert_telegram_identity,
)
from apps.partners.models import Partner
from core.security.crypto import decrypt_text, encrypt_text
from core.security.fingerprints import token_fingerprint
from core.security.masking import mask_sensitive_text, mask_token

MASTER_CONTACT_LINK_PREFIX = "admin"
MASTER_CONTACT_LINK_TTL = timedelta(hours=24)
MASTER_CONTACT_SIGNATURE_LENGTH = 16


@dataclass(frozen=True, slots=True)
class RenderedTemplate:
    """Resolved bot content and metadata useful for previews/debugging."""

    key: str
    language: str
    body: str
    source: str


@dataclass(frozen=True, slots=True)
class MasterContactLink:
    """Signed Telegram deep link for binding the partner owner/master contact."""

    url: str
    start_payload: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class StartCommandResult:
    """Result of processing a Telegram /start command in partner context."""

    text: str
    telegram_identity: TelegramIdentity
    client_profile_id: str = ""
    master_contact_id: str = ""
    master_contact_bound: bool = False


def set_bot_token(*, actor: User, partner: Partner, raw_token: str) -> BotInstance:
    """Create or update a partner bot token with encryption and duplicate safety."""

    ensure_partner_access(actor, partner)
    token = validate_bot_token_format(raw_token)
    fingerprint = token_fingerprint(token)
    duplicate = (
        BotInstance.objects.filter(token_fingerprint=fingerprint)
        .exclude(partner=partner)
        .exists()
    )
    if duplicate:
        raise ValidationError({"token": "This Telegram bot token is already used."})

    encrypted = encrypt_text(token)
    with transaction.atomic():
        bot_instance, _created = BotInstance.objects.select_for_update().get_or_create(
            partner=partner,
            defaults={"status": BotInstance.Status.PAUSED},
        )
        token_changed = bot_instance.token_fingerprint != fingerprint
        bot_instance.encrypted_token = encrypted.ciphertext
        bot_instance.token_key_id = encrypted.key_id
        bot_instance.token_fingerprint = fingerprint
        bot_instance.token_masked = mask_token(token)
        bot_instance.last_error_code = ""
        bot_instance.last_error_message_masked = ""
        if token_changed:
            bot_instance.telegram_bot_id = None
            bot_instance.telegram_username = ""
            bot_instance.last_checked_at = None
        if bot_instance.status == BotInstance.Status.ERROR:
            bot_instance.status = BotInstance.Status.PAUSED
        elif token_changed and bot_instance.status != BotInstance.Status.SUSPENDED:
            bot_instance.status = BotInstance.Status.PAUSED
        bot_instance.save()

    return bot_instance


def handle_start_command(
    *,
    partner: Partner,
    telegram_user_id: int,
    username: str = "",
    first_name: str = "",
    last_name: str = "",
    is_premium: bool = False,
    language_code: str = "",
    start_payload: str = "",
) -> StartCommandResult:
    """Process Telegram `/start` while preserving trusted partner context."""

    identity = upsert_telegram_identity(
        telegram_user_id=telegram_user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        is_premium=is_premium,
        language_code=language_code,
    )
    if start_payload.startswith(f"{MASTER_CONTACT_LINK_PREFIX}_"):
        contact = bind_master_contact_from_start_payload(
            partner=partner,
            start_payload=start_payload,
            telegram_identity=identity,
        )
        text = render_content_template(
            partner=partner,
            key="admin.master_contact_linked",
            language=partner.default_language,
        ).body
        return StartCommandResult(
            text=text,
            telegram_identity=identity,
            master_contact_id=str(contact.id),
            master_contact_bound=True,
        )

    profile = resolve_client_profile_for_telegram_identity(
        partner=partner,
        telegram_identity=identity,
    )
    text = render_content_template(
        partner=partner,
        key="start.welcome",
        language=profile.language,
        context={"partner_name": partner.name},
    ).body
    return StartCommandResult(
        text=text,
        telegram_identity=identity,
        client_profile_id=str(profile.id),
    )


def resolve_client_profile_from_telegram_user(
    *,
    partner: Partner,
    telegram_user_id: int,
    username: str = "",
    first_name: str = "",
    last_name: str = "",
    is_premium: bool = False,
    language_code: str = "",
):
    """Resolve global Telegram identity and partner-scoped client profile."""

    identity = upsert_telegram_identity(
        telegram_user_id=telegram_user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        is_premium=is_premium,
        language_code=language_code,
    )
    profile = resolve_client_profile_for_telegram_identity(
        partner=partner,
        telegram_identity=identity,
    )
    return identity, profile


def render_invalid_master_contact_link_message(*, partner: Partner) -> str:
    """Return localized text for an invalid/expired admin Telegram link."""

    return render_content_template(
        partner=partner,
        key="admin.master_contact_link_invalid",
        language=partner.default_language,
    ).body


def create_master_contact_link(
    *,
    actor: User,
    partner: Partner,
    now=None,
) -> MasterContactLink:
    """Create a short-lived signed deep link for binding master Telegram delivery.

    Telegram start payloads are intentionally short. We therefore keep only a
    signed nonce in the bot link and store the nonce fingerprint server-side.
    """

    ensure_partner_access(actor, partner)
    bot_instance = BotInstance.objects.filter(partner=partner).first()
    if bot_instance is None or not bot_instance.telegram_username:
        raise ValidationError(
            {"bot": "Bot connection must be checked before creating a Telegram contact link."}
        )

    current_time = now or timezone.now()
    nonce = secrets.token_urlsafe(16)
    signature = _master_contact_nonce_signature(nonce)
    start_payload = f"{MASTER_CONTACT_LINK_PREFIX}_{nonce}_{signature}"
    expires_at = current_time + MASTER_CONTACT_LINK_TTL
    PartnerTelegramContactLink.objects.create(
        partner=partner,
        user=actor,
        nonce_fingerprint=_master_contact_nonce_fingerprint(nonce),
        expires_at=expires_at,
    )
    username = bot_instance.telegram_username.lstrip("@")
    return MasterContactLink(
        url=f"https://t.me/{username}?start={start_payload}",
        start_payload=start_payload,
        expires_at=expires_at,
    )


def bind_master_contact_from_start_payload(
    *,
    partner: Partner,
    start_payload: str,
    telegram_identity: TelegramIdentity,
    now=None,
) -> PartnerTelegramContact:
    """Bind a Telegram identity as a partner master contact from a signed payload."""

    current_time = now or timezone.now()
    nonce = _nonce_from_master_contact_payload(start_payload)
    with transaction.atomic():
        try:
            link = (
                PartnerTelegramContactLink.objects.select_for_update()
                .select_related("partner", "user")
                .get(
                    partner=partner,
                    nonce_fingerprint=_master_contact_nonce_fingerprint(nonce),
                )
            )
        except PartnerTelegramContactLink.DoesNotExist as exc:
            raise ValidationError(
                {"start_payload": "Telegram contact link was not found."}
            ) from exc
        if link.consumed_at is not None:
            raise ValidationError({"start_payload": "This Telegram contact link was already used."})
        if link.expires_at <= current_time:
            raise ValidationError({"start_payload": "This Telegram contact link has expired."})

        has_active_primary = PartnerTelegramContact.objects.filter(
            partner=partner,
            is_active=True,
            is_primary=True,
        ).exists()
        contact, created = PartnerTelegramContact.objects.select_for_update().get_or_create(
            partner=partner,
            telegram_identity=telegram_identity,
            defaults={
                "user": link.user,
                "role": PartnerTelegramContact.Role.OWNER_MASTER,
                "is_primary": not has_active_primary,
                "is_active": True,
                "linked_at": current_time,
            },
        )
        update_fields: list[str] = []
        if not created:
            if contact.user_id != link.user_id:
                contact.user = link.user
                update_fields.append("user")
            if not contact.is_active:
                contact.is_active = True
                update_fields.append("is_active")
            if not has_active_primary:
                contact.is_primary = True
                update_fields.append("is_primary")
            contact.linked_at = current_time
            update_fields.append("linked_at")
            if contact.last_delivery_failure_code or contact.last_delivery_failure_at:
                contact.last_delivery_failure_code = ""
                contact.last_delivery_failure_at = None
                update_fields.extend(
                    ["last_delivery_failure_code", "last_delivery_failure_at"]
                )
            if update_fields:
                update_fields.append("updated_at")
                contact.save(update_fields=update_fields)

        link.consumed_at = current_time
        link.save(update_fields=["consumed_at", "updated_at"])
        return contact


def mark_master_contact_delivery_failure(
    *,
    contact: PartnerTelegramContact,
    failure_code: str,
) -> PartnerTelegramContact:
    """Persist the last terminal delivery problem for a master contact."""

    contact.last_delivery_failure_code = failure_code[:128]
    contact.last_delivery_failure_at = timezone.now()
    contact.save(
        update_fields=[
            "last_delivery_failure_code",
            "last_delivery_failure_at",
            "updated_at",
        ]
    )
    return contact


def mark_master_contact_reachable(*, contact: PartnerTelegramContact) -> PartnerTelegramContact:
    """Clear delivery failure markers after successful master-facing contact."""

    contact.last_delivery_failure_code = ""
    contact.last_delivery_failure_at = None
    contact.save(
        update_fields=[
            "last_delivery_failure_code",
            "last_delivery_failure_at",
            "updated_at",
        ]
    )
    return contact


def decrypt_bot_token(bot_instance: BotInstance) -> str:
    """Decrypt a stored bot token through the shared key-ring helper."""

    if not bot_instance.encrypted_token:
        raise ValidationError("Bot token is missing.")
    return decrypt_text(bot_instance.encrypted_token, key_id=bot_instance.token_key_id)


def set_bot_status(*, actor: User, bot_instance: BotInstance, status: str) -> BotInstance:
    """Change bot operational status while protecting superuser-only states."""

    ensure_partner_access(actor, bot_instance.partner)
    if status not in BotInstance.Status.values:
        raise ValidationError({"status": "Unknown bot status."})
    if status == BotInstance.Status.SUSPENDED and not actor.is_superuser:
        raise PermissionDenied("Only a superuser can suspend a bot.")
    if bot_instance.status == BotInstance.Status.SUSPENDED and not actor.is_superuser:
        raise PermissionDenied("Only a superuser can change a suspended bot.")
    if status == BotInstance.Status.ACTIVE and not bot_instance.has_token:
        raise ValidationError({"status": "A bot token is required before activation."})

    bot_instance.status = status
    bot_instance.save(update_fields=["status", "updated_at"])
    return bot_instance


def mark_bot_error(
    bot_instance: BotInstance,
    *,
    code: str,
    message: str,
) -> BotInstance:
    """Persist a business-friendly runtime/configuration error for admin views."""

    bot_instance.status = BotInstance.Status.ERROR
    bot_instance.last_error_code = code
    bot_instance.last_error_message_masked = mask_sensitive_text(message)[:500]
    bot_instance.last_checked_at = timezone.now()
    bot_instance.save(
        update_fields=[
            "status",
            "last_error_code",
            "last_error_message_masked",
            "last_checked_at",
            "updated_at",
        ]
    )
    return bot_instance


def check_bot_connection(bot_instance: BotInstance, *, actor: User | None = None) -> BotInstance:
    """Run an explicit Telegram getMe check and persist safe health fields."""

    if actor is not None:
        ensure_partner_access(actor, bot_instance.partner)

    try:
        token = decrypt_bot_token(bot_instance)
        identity = async_to_sync(fetch_bot_identity)(token)
    except (ImproperlyConfigured, ValidationError) as exc:
        return mark_bot_error(
            bot_instance,
            code="bot_config_invalid",
            message=str(exc),
        )
    except Exception as exc:
        return mark_bot_error(
            bot_instance,
            code="telegram_connection_failed",
            message=str(exc),
        )

    update_fields = [
        "telegram_bot_id",
        "telegram_username",
        "last_error_code",
        "last_error_message_masked",
        "last_checked_at",
        "updated_at",
    ]
    bot_instance.telegram_bot_id = identity.telegram_bot_id
    bot_instance.telegram_username = identity.username
    bot_instance.last_error_code = ""
    bot_instance.last_error_message_masked = ""
    bot_instance.last_checked_at = timezone.now()
    if bot_instance.status == BotInstance.Status.ERROR:
        bot_instance.status = BotInstance.Status.PAUSED
        update_fields.append("status")
    bot_instance.save(update_fields=update_fields)
    return bot_instance


def seed_system_templates() -> int:
    """Synchronize code-owned MVP content defaults into editable DB records."""

    changed = 0
    for definition in SYSTEM_TEMPLATE_DEFINITIONS:
        for language, body in definition.translations.items():
            template, created = SystemTemplate.objects.update_or_create(
                key=definition.key,
                language=language,
                defaults={
                    "body": body,
                    "content_type": definition.content_type,
                    "editable": definition.editable,
                    "is_required": definition.is_required,
                    "placeholder_allowlist": list(definition.placeholders),
                    "sample_context": dict(definition.sample_context),
                },
            )
            changed += int(created or bool(template.updated_at))
    return changed


def set_partner_template_override(
    *,
    actor: User,
    partner: Partner,
    key: str,
    language: str,
    body: str,
    is_active: bool = True,
) -> PartnerTemplateOverride:
    """Create or update a partner localized override after strict validation."""

    ensure_partner_access(actor, partner)
    cleaned_key = key.strip()
    cleaned_language = _normalize_template_language(language)
    system_template = _system_template_for_key_language(
        key=cleaned_key,
        language=cleaned_language,
    )
    if not system_template.editable:
        raise ValidationError({"key": "This template is system-only."})

    cleaned_body = body.strip()
    if cleaned_body:
        validate_template_body(
            body=cleaned_body,
            allowed_placeholders=tuple(system_template.placeholder_allowlist),
            sample_context=system_template.sample_context,
            content_type=system_template.content_type,
        )

    override, _created = PartnerTemplateOverride.objects.update_or_create(
        partner=partner,
        key=cleaned_key,
        language=cleaned_language,
        defaults={
            "body": cleaned_body,
            "is_active": bool(is_active),
            "last_validated_at": timezone.now(),
        },
    )
    return override


def reset_partner_template_override(
    *,
    actor: User,
    partner: Partner,
    key: str,
    language: str,
) -> int:
    """Remove a partner override so runtime falls back to the system default."""

    ensure_partner_access(actor, partner)
    return PartnerTemplateOverride.objects.filter(
        partner=partner,
        key=key.strip(),
        language=_normalize_template_language(language),
    ).delete()[0]


def render_content_template(
    *,
    partner: Partner | None,
    key: str,
    language: str,
    context: dict | None = None,
) -> RenderedTemplate:
    """Render partner-aware localized content with per-language fallback rules."""

    cleaned_key = key.strip()
    normalized_language = _normalize_template_language(language, allow_default=True)
    render_context = _SafeTemplateContext(context or {})

    override = None
    if partner is not None:
        override = (
            PartnerTemplateOverride.objects.filter(
                partner=partner,
                key=cleaned_key,
                language=normalized_language,
                is_active=True,
            )
            .exclude(body="")
            .first()
        )

    if override is not None:
        system_template = _system_template_for_key_language(
            key=cleaned_key,
            language=normalized_language,
        )
        try:
            validate_template_body(
                body=override.body,
                allowed_placeholders=tuple(system_template.placeholder_allowlist),
                sample_context=system_template.sample_context,
                content_type=system_template.content_type,
            )
            return RenderedTemplate(
                key=cleaned_key,
                language=normalized_language,
                body=_render_template_body(override.body, render_context),
                source="partner_override",
            )
        except ValidationError:
            # Runtime stays defensive even if invalid legacy data somehow exists.
            pass

    system_template = _system_template_for_key_language(
        key=cleaned_key,
        language=normalized_language,
    )
    return RenderedTemplate(
        key=cleaned_key,
        language=system_template.language,
        body=_render_template_body(system_template.body, render_context),
        source="system_default",
    )


def validate_template_body(
    *,
    body: str,
    allowed_placeholders: tuple[str, ...],
    sample_context: dict,
    content_type: str = BotTemplateContentType.MESSAGE,
) -> None:
    """Validate placeholder use and basic Telegram-friendly label constraints."""

    if not body.strip():
        raise ValidationError({"body": "Template body cannot be empty."})

    placeholders = extract_template_placeholders(body)
    unknown = sorted(set(placeholders) - set(allowed_placeholders))
    if unknown:
        raise ValidationError({"body": f"Unknown placeholder(s): {', '.join(unknown)}."})

    try:
        _render_template_body(body, _SafeTemplateContext(sample_context))
    except Exception as exc:
        raise ValidationError({"body": "Template cannot be rendered."}) from exc

    if content_type == BotTemplateContentType.BUTTON and len(body.strip()) > 64:
        raise ValidationError({"body": "Button labels must be 64 characters or shorter."})


def extract_template_placeholders(body: str) -> tuple[str, ...]:
    """Return unique Python-format placeholders used by a content template."""

    placeholders: list[str] = []
    for _literal, field_name, _format_spec, _conversion in Formatter().parse(body):
        if not field_name:
            continue
        root_name = field_name.split(".", 1)[0].split("[", 1)[0]
        if root_name and root_name not in placeholders:
            placeholders.append(root_name)
    return tuple(placeholders)


class _SafeTemplateContext(dict):
    def __missing__(self, key):
        return ""


def _render_template_body(body: str, context: _SafeTemplateContext) -> str:
    return body.format_map(context)


def _system_template_for_key_language(*, key: str, language: str) -> SystemTemplate:
    normalized_language = _normalize_template_language(language, allow_default=True)
    template = SystemTemplate.objects.filter(key=key, language=normalized_language).first()
    if template is not None:
        return template

    definition = SYSTEM_TEMPLATE_DEFINITION_BY_KEY.get(key)
    if definition is None:
        raise ValidationError({"key": "Unknown content template key."})

    body = definition.translations.get(normalized_language)
    resolved_language = normalized_language
    if body is None:
        body = definition.translations[DEFAULT_TEMPLATE_LANGUAGE]
        resolved_language = DEFAULT_TEMPLATE_LANGUAGE

    return SystemTemplate(
        key=definition.key,
        language=resolved_language,
        body=body,
        content_type=definition.content_type,
        editable=definition.editable,
        is_required=definition.is_required,
        placeholder_allowlist=list(definition.placeholders),
        sample_context=dict(definition.sample_context),
    )


def _normalize_template_language(language: str, *, allow_default: bool = False) -> str:
    cleaned_language = (language or "").strip().lower()
    if allow_default and not cleaned_language:
        return DEFAULT_TEMPLATE_LANGUAGE
    if cleaned_language not in SUPPORTED_TEMPLATE_LANGUAGES:
        if allow_default:
            return DEFAULT_TEMPLATE_LANGUAGE
        raise ValidationError({"language": "Unsupported template language."})
    return cleaned_language


def _nonce_from_master_contact_payload(start_payload: str) -> str:
    prefix = f"{MASTER_CONTACT_LINK_PREFIX}_"
    if not start_payload.startswith(prefix):
        raise ValidationError({"start_payload": "Unsupported Telegram start payload."})
    try:
        nonce, signature = start_payload[len(prefix) :].rsplit("_", 1)
    except ValueError as exc:
        raise ValidationError({"start_payload": "Malformed Telegram start payload."}) from exc
    if not nonce or not signature:
        raise ValidationError({"start_payload": "Malformed Telegram start payload."})
    expected = _master_contact_nonce_signature(nonce)
    if not constant_time_compare(signature, expected):
        raise ValidationError({"start_payload": "Invalid Telegram contact link signature."})
    return nonce


def _master_contact_nonce_signature(nonce: str) -> str:
    return salted_hmac(
        "bot.master_contact_link.signature",
        nonce,
    ).hexdigest()[:MASTER_CONTACT_SIGNATURE_LENGTH]


def _master_contact_nonce_fingerprint(nonce: str) -> str:
    return salted_hmac(
        "bot.master_contact_link.fingerprint",
        nonce,
    ).hexdigest()
