from decimal import Decimal

import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from core.money import Money
from core.security.crypto import decrypt_text, encrypt_text
from core.security.fingerprints import token_fingerprint
from core.security.masking import mask_phone, mask_token, redact_mapping


def test_money_uses_minor_units() -> None:
    money = Money.from_major("500.00")

    assert money.amount == 50000
    assert money.currency == "UAH"
    assert money.to_major_decimal() == Decimal("500.00")


def test_mask_token_keeps_safe_shape() -> None:
    assert mask_token("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ") == "123456:***WXYZ"


def test_mask_phone_keeps_only_last_digits() -> None:
    assert mask_phone("+380501234567") == "***4567"


def test_redact_mapping_masks_nested_sensitive_values() -> None:
    value = {
        "safe": "ok",
        "bot_token": "secret",
        "nested": {"phone_number": "+380501234567"},
    }

    assert redact_mapping(value) == {
        "safe": "ok",
        "bot_token": "***",
        "nested": {"phone_number": "***"},
    }


def test_crypto_key_ring_encrypts_and_decrypts() -> None:
    key = Fernet.generate_key().decode("utf-8")

    with override_settings(
        BOT_TOKEN_ENCRYPTION_KEYS={"main": key},
        BOT_TOKEN_CURRENT_KEY_ID="main",
    ):
        encrypted = encrypt_text("123456:test-token")

        assert encrypted.key_id == "main"
        assert encrypted.ciphertext != "123456:test-token"
        assert decrypt_text(encrypted.ciphertext, key_id=encrypted.key_id) == "123456:test-token"


def test_crypto_raises_without_key_ring() -> None:
    with override_settings(BOT_TOKEN_ENCRYPTION_KEYS={}, BOT_TOKEN_CURRENT_KEY_ID=""):
        with pytest.raises(ImproperlyConfigured):
            encrypt_text("token")


def test_token_fingerprint_is_stable_and_non_reversible() -> None:
    with override_settings(TOKEN_FINGERPRINT_KEY="fingerprint-secret"):
        first = token_fingerprint("raw-token")
        second = token_fingerprint("raw-token")

    assert first == second
    assert first != "raw-token"
    assert len(first) == 64
