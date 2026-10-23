from __future__ import annotations

from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


@dataclass(frozen=True, slots=True)
class EncryptedText:
    ciphertext: str
    key_id: str


def _configured_keys() -> dict[str, str]:
    keys = getattr(settings, "BOT_TOKEN_ENCRYPTION_KEYS", {})
    if not keys:
        raise ImproperlyConfigured("BOT_TOKEN_ENCRYPTION_KEYS must be configured.")
    return keys


def _fernet_for_key(key: str) -> Fernet:
    return Fernet(key.encode("utf-8"))


def encrypt_text(value: str, *, key_id: str | None = None) -> EncryptedText:
    keys = _configured_keys()
    selected_key_id = key_id or getattr(settings, "BOT_TOKEN_CURRENT_KEY_ID", "")
    if not selected_key_id:
        raise ImproperlyConfigured("BOT_TOKEN_CURRENT_KEY_ID must be configured.")
    try:
        key = keys[selected_key_id]
    except KeyError as exc:
        raise ImproperlyConfigured("Current token encryption key id is not configured.") from exc
    ciphertext = _fernet_for_key(key).encrypt(value.encode("utf-8")).decode("utf-8")
    return EncryptedText(ciphertext=ciphertext, key_id=selected_key_id)


def decrypt_text(ciphertext: str, *, key_id: str | None = None) -> str:
    keys = _configured_keys()
    ordered_keys: list[str] = []
    if key_id and key_id in keys:
        ordered_keys.append(keys[key_id])
    ordered_keys.extend(key for current_id, key in keys.items() if current_id != key_id)
    try:
        plaintext = MultiFernet([_fernet_for_key(key) for key in ordered_keys]).decrypt(
            ciphertext.encode("utf-8")
        )
    except InvalidToken as exc:
        raise ImproperlyConfigured("Unable to decrypt value with configured key ring.") from exc
    return plaintext.decode("utf-8")
