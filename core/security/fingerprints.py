from __future__ import annotations

import hashlib
import hmac

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def token_fingerprint(raw_token: str) -> str:
    secret = getattr(settings, "TOKEN_FINGERPRINT_KEY", "")
    if not secret:
        raise ImproperlyConfigured("TOKEN_FINGERPRINT_KEY must be configured.")
    return hmac.new(secret.encode("utf-8"), raw_token.encode("utf-8"), hashlib.sha256).hexdigest()
