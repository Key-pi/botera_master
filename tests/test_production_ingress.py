from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_SECRET = "production-only-secret-0123456789abcdef0123456789abcdef"
PRODUCTION_FERNET_KEY = Fernet.generate_key().decode("utf-8")


def _production_environment(**overrides: str) -> dict[str, str]:
    environment = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "core.settings.production",
        "DJANGO_SECRET_KEY": PRODUCTION_SECRET,
        "DJANGO_ALLOWED_HOSTS": "master.example.com",
        "DJANGO_CSRF_TRUSTED_ORIGINS": "https://master.example.com",
        "BOTERA_DOMAIN": "master.example.com",
        "POSTGRES_DB": "botera_master",
        "POSTGRES_USER": "botera_master",
        "POSTGRES_PASSWORD": "database-only-secret",
        "POSTGRES_HOST": "postgres",
        "REDIS_PASSWORD": "redis-only-secret-0123456789abcdef",
        "BOT_TOKEN_ENCRYPTION_KEYS": f"main:{PRODUCTION_FERNET_KEY}",
        "BOT_TOKEN_CURRENT_KEY_ID": "main",
        "TOKEN_FINGERPRINT_KEY": "fingerprint-only-secret-0123456789abcdef",
        "BOTERA_ENV_FILE": ".env.example",
        "BOTERA_PRODUCTION_ENV_FILE": ".env.production.example",
    }
    environment.pop("DATABASE_URL", None)
    environment.update(overrides)
    return environment


def test_production_settings_enable_proxy_and_cookie_security() -> None:
    probe = """
from django.conf import settings

assert settings.DEBUG is False
assert settings.ALLOWED_HOSTS == ["master.example.com"]
assert settings.CSRF_TRUSTED_ORIGINS == ["https://master.example.com"]
assert settings.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")
assert settings.SECURE_SSL_REDIRECT is True
assert settings.SESSION_COOKIE_SECURE is True
assert settings.CSRF_COOKIE_SECURE is True
assert settings.SECURE_HSTS_SECONDS == 31_536_000
assert settings.STATIC_URL == "/static/"
"""

    subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPOSITORY_ROOT,
        env=_production_environment(),
        check=True,
        capture_output=True,
        text=True,
    )


def test_production_settings_reject_weak_secret() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "from django.conf import settings; print(settings.DEBUG)"],
        cwd=REPOSITORY_ROOT,
        env=_production_environment(DJANGO_SECRET_KEY="change-me"),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "DJANGO_SECRET_KEY must be a production-only random value" in result.stderr


def test_production_settings_reject_default_postgres_password() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "from django.conf import settings; print(settings.DEBUG)"],
        cwd=REPOSITORY_ROOT,
        env=_production_environment(
            POSTGRES_DB="botera_master",
            POSTGRES_PASSWORD="change-me",
        ),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "POSTGRES_PASSWORD must be a production-only value" in result.stderr


def test_production_settings_reject_invalid_bot_encryption_key() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "from django.conf import settings; print(settings.DEBUG)"],
        cwd=REPOSITORY_ROOT,
        env=_production_environment(BOT_TOKEN_ENCRYPTION_KEYS="main:not-a-fernet-key"),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "not a valid Fernet key" in result.stderr


def test_production_settings_reject_unknown_current_bot_key() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "from django.conf import settings; print(settings.DEBUG)"],
        cwd=REPOSITORY_ROOT,
        env=_production_environment(BOT_TOKEN_CURRENT_KEY_ID="missing"),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "must reference a configured encryption key" in result.stderr


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker Compose is unavailable")
def test_production_compose_exposes_only_caddy_and_uses_image_code() -> None:
    result = subprocess.run(
        [
            str(REPOSITORY_ROOT / "scripts" / "compose.sh"),
            "prod",
            "config",
            "--format",
            "json",
            "--no-env-resolution",
        ],
        cwd=REPOSITORY_ROOT,
        env=_production_environment(),
        check=True,
        capture_output=True,
        text=True,
    )
    config = json.loads(result.stdout)
    services = config["services"]

    assert config["name"] == "botera-master-production"

    for private_service in ("postgres", "redis", "web"):
        assert not services[private_service].get("ports")

    published_ports = {
        (port["target"], port["protocol"]): port["published"]
        for port in services["caddy"]["ports"]
    }
    assert published_ports == {
        (80, "tcp"): "80",
        (443, "tcp"): "443",
        (443, "udp"): "443",
    }

    app_services = (
        "init",
        "web",
        "bot-runner",
        "celery-beat",
        "notifications-worker",
        "broadcast-worker",
        "default-worker",
    )
    for service_name in app_services:
        assert all(
            volume.get("target") != "/app"
            for volume in services[service_name].get("volumes", ())
        )
        assert services[service_name]["environment"]["DJANGO_SETTINGS_MODULE"] == (
            "core.settings.production"
        )
        assert services[service_name]["user"] == "app"

    assert services["redis"]["command"][:2] == ["redis-server", "--requirepass"]
    assert services["redis"]["volumes"][0]["target"] == "/data"

    web_command = " ".join(services["web"]["command"])
    assert "gunicorn core.wsgi:application" in web_command
    assert "--access-logfile" not in web_command
    healthcheck_command = " ".join(services["web"]["healthcheck"]["test"])
    assert "X-Forwarded-Proto" in healthcheck_command
    assert "DJANGO_ALLOWED_HOSTS" in healthcheck_command
    assert services["web"]["volumes"] == [
        {
            "type": "volume",
            "source": "static_data",
            "target": "/app/staticfiles",
            "volume": {},
        }
    ]
