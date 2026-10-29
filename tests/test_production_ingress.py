from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_SECRET = "production-only-secret-0123456789abcdef0123456789abcdef"


def _production_environment(**overrides: str) -> dict[str, str]:
    environment = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "core.settings.production",
        "DJANGO_SECRET_KEY": PRODUCTION_SECRET,
        "DJANGO_ALLOWED_HOSTS": "master.example.com",
        "DJANGO_CSRF_TRUSTED_ORIGINS": "https://master.example.com",
        "BOTERA_DOMAIN": "master.example.com",
        "POSTGRES_PASSWORD": "database-only-secret",
        "BOT_TOKEN_ENCRYPTION_KEYS": "main:test-fernet-key",
        "BOT_TOKEN_CURRENT_KEY_ID": "main",
        "TOKEN_FINGERPRINT_KEY": "fingerprint-only-secret",
    }
    environment.pop("DATABASE_URL", None)
    environment.pop("POSTGRES_DB", None)
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


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker Compose is unavailable")
def test_production_compose_exposes_only_caddy_and_uses_image_code() -> None:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "docker-compose.yml",
            "-f",
            "docker-compose.production.yml",
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
    services = json.loads(result.stdout)["services"]

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
