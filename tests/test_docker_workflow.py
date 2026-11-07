from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

from apps.accounts.models import PartnerMembership, User
from apps.bot.management.commands.run_partner_bots import Command as RunBotsCommand
from apps.bot.runtime import RuntimeLoadResult
from apps.catalog.models import Service, ServiceCategory
from apps.partners.models import Partner
from apps.schedule.models import AvailabilityWindow, PartnerScheduleSettings

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_healthcheck_is_available_without_authentication(client) -> None:
    response = client.get("/health/")

    assert response.status_code == 200
    assert response.content == b"ok"
    assert response["Content-Type"].startswith("text/plain")


@pytest.mark.django_db
def test_seed_pilot_demo_is_idempotent_and_does_not_print_passwords(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_ADMIN_USERNAME", "docker-platform-admin")
    monkeypatch.setenv("DEMO_ADMIN_PASSWORD", "local-admin-password")
    monkeypatch.setenv("DEMO_OWNER_USERNAME", "docker-preview-owner")
    monkeypatch.setenv("DEMO_OWNER_PASSWORD", "local-owner-password")
    monkeypatch.setenv("DEMO_PARTNER_NAME", "Docker Preview Studio")
    output = StringIO()

    call_command("seed_pilot_demo", stdout=output)
    call_command("seed_pilot_demo", stdout=output)

    partner = Partner.objects.get(name="Docker Preview Studio")
    owner = User.objects.get(username="docker-preview-owner")
    admin = User.objects.get(username="docker-platform-admin")
    assert admin.is_superuser is True
    assert admin.check_password("local-admin-password") is True
    assert owner.check_password("local-owner-password") is True
    assert PartnerMembership.objects.filter(
        partner=partner,
        user=owner,
        role=PartnerMembership.Role.OWNER,
        is_active=True,
    ).exists()
    assert ServiceCategory.objects.filter(partner=partner).count() == 1
    assert Service.objects.filter(partner=partner).count() == 2
    assert AvailabilityWindow.objects.filter(partner=partner).count() == 7
    assert (
        PartnerScheduleSettings.objects.get(partner=partner).confirmation_mode
        == PartnerScheduleSettings.ConfirmationMode.MANUAL
    )
    rendered = output.getvalue()
    assert "local-admin-password" not in rendered
    assert "local-owner-password" not in rendered


def test_empty_bot_runner_can_wait_for_an_explicit_restart(monkeypatch) -> None:
    waited = []

    async def fake_wait_until_stopped() -> None:
        waited.append(True)

    monkeypatch.setattr(
        "apps.bot.management.commands.run_partner_bots.load_runtime_bot_configs",
        lambda: RuntimeLoadResult(configs=(), skipped=()),
    )
    monkeypatch.setattr(
        "apps.bot.management.commands.run_partner_bots.wait_until_stopped",
        fake_wait_until_stopped,
    )
    output = StringIO()

    RunBotsCommand(stdout=output).handle(dry_run=False, wait=True)

    assert waited == [True]
    assert "restart it after launching a partner bot" in output.getvalue()


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker Compose is unavailable")
def test_local_compose_has_ordered_isolated_application_stack() -> None:
    environment = {
        **os.environ,
        "BOTERA_ENV_FILE": ".env.example",
    }
    result = subprocess.run(
        [
            str(REPOSITORY_ROOT / "scripts" / "compose.sh"),
            "dev",
            "config",
            "--format",
            "json",
            "--no-env-resolution",
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    config = json.loads(result.stdout)
    services = config["services"]

    assert config["name"] == "botera-master"
    assert not services["postgres"].get("ports")
    assert not services["redis"].get("ports")
    assert services["redis"]["volumes"] == [
        {
            "type": "volume",
            "source": "redis_data",
            "target": "/data",
            "volume": {},
        }
    ]
    assert services["init"]["command"] == ["sh", "infrastructure/docker/init.sh"]

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
        assert services[service_name]["user"] == "app"
        assert services[service_name]["environment"]["DJANGO_SETTINGS_MODULE"] == (
            "core.settings.local"
        )
        if service_name != "init":
            assert services[service_name]["depends_on"]["init"]["condition"] == (
                "service_completed_successfully"
            )

    assert services["bot-runner"]["command"][-1] == "--wait"
    assert "--schedule=/tmp/celerybeat-schedule" in services["celery-beat"]["command"]
    assert "/health/" in " ".join(services["web"]["healthcheck"]["test"])


def test_compose_launcher_rejects_unknown_mode() -> None:
    result = subprocess.run(
        [str(REPOSITORY_ROOT / "scripts" / "compose.sh"), "staging", "config"],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "Unknown Docker mode: staging" in result.stderr


def test_compose_init_syncs_content_after_migrations_before_services_start() -> None:
    init_script = (REPOSITORY_ROOT / "infrastructure" / "docker" / "init.sh").read_text()

    migration_index = init_script.index("python manage.py migrate --noinput")
    content_index = init_script.index("python manage.py sync_system_templates")
    owner_access_index = init_script.index("python manage.py sync_partner_owner_access")

    assert migration_index < content_index < owner_access_index


def test_postgres_test_settings_keep_compose_database_and_deterministic_language() -> None:
    environment = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "core.settings.postgres_test",
        "POSTGRES_DB": "botera_test_source",
        "POSTGRES_USER": "botera_test_user",
        "POSTGRES_PASSWORD": "test-only-password",
        "POSTGRES_HOST": "postgres",
        "POSTGRES_PORT": "5432",
    }
    script = """
import json
import django

django.setup()
from django.conf import settings

print(json.dumps({
    "engine": settings.DATABASES["default"]["ENGINE"],
    "name": settings.DATABASES["default"]["NAME"],
    "language": settings.LANGUAGE_CODE,
    "celery_eager": settings.CELERY_TASK_ALWAYS_EAGER,
}))
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    settings_snapshot = json.loads(result.stdout)

    assert settings_snapshot == {
        "engine": "django.db.backends.postgresql",
        "name": "botera_test_source",
        "language": "en",
        "celery_eager": True,
    }
