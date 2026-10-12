from __future__ import annotations

from asgiref.sync import async_to_sync
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.services import ensure_partner_access
from apps.bot.models import BotInstance
from apps.bot.telegram import fetch_bot_identity
from apps.bot.token_validation import validate_bot_token_format
from apps.partners.models import Partner
from core.security.crypto import decrypt_text, encrypt_text
from core.security.fingerprints import token_fingerprint
from core.security.masking import mask_sensitive_text, mask_token


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
    now = timezone.now()
    with transaction.atomic():
        bot_instance, _created = BotInstance.objects.select_for_update().get_or_create(
            partner=partner,
            defaults={"status": BotInstance.Status.PAUSED},
        )
        bot_instance.encrypted_token = encrypted.ciphertext
        bot_instance.token_key_id = encrypted.key_id
        bot_instance.token_fingerprint = fingerprint
        bot_instance.token_masked = mask_token(token)
        bot_instance.last_error_code = ""
        bot_instance.last_error_message_masked = ""
        bot_instance.last_checked_at = now
        if bot_instance.status == BotInstance.Status.ERROR:
            bot_instance.status = BotInstance.Status.PAUSED
        bot_instance.save()

    return bot_instance


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
