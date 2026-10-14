from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.accounts.models import User
from apps.bot.models import BotInstance, PartnerTelegramContact
from apps.broadcasts.services import BroadcastDispatchSummary
from apps.broadcasts.tasks import dispatch_due_recipients_task
from apps.catalog.models import Service, ServiceCategory
from apps.clients.models import TelegramIdentity
from apps.partners.models import Partner
from apps.partners.services import create_partner_with_owner
from apps.schedule.models import AvailabilityWindow, PartnerScheduleSettings
from apps.schedule.selectors import default_booking_resource_for_partner


@pytest.fixture
def pilot_ready_partner():
    superuser = User.objects.create_superuser(username="pilot-root", password="secret")
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Pilot Ready",
        owner_username="pilot-ready-owner",
        owner_password="secret",
    )
    partner = created.partner
    category = ServiceCategory.objects.create(partner=partner, name="Nails")
    Service.objects.create(
        partner=partner,
        category=category,
        name="Manicure",
        price_amount=50000,
        duration_minutes=60,
    )
    resource = default_booking_resource_for_partner(partner)
    assert resource is not None
    AvailabilityWindow.objects.create(
        partner=partner,
        booking_resource=resource,
        start_at=timezone.now() + timedelta(days=1),
        end_at=timezone.now() + timedelta(days=1, hours=4),
    )
    PartnerScheduleSettings.objects.filter(partner=partner).update(
        confirmation_mode=PartnerScheduleSettings.ConfirmationMode.MANUAL,
    )
    BotInstance.objects.create(
        partner=partner,
        status=BotInstance.Status.ACTIVE,
        encrypted_token="encrypted-value",
        token_key_id="pilot",
        token_fingerprint="pilot-fingerprint",
        token_masked="123456:***WXYZ",
        telegram_bot_id=123456,
        telegram_username="pilot_ready_bot",
        last_checked_at=timezone.now(),
    )
    identity = TelegramIdentity.objects.create(
        telegram_user_id=987654321,
        username="pilot_master",
    )
    PartnerTelegramContact.objects.create(
        partner=partner,
        telegram_identity=identity,
        user=created.owner,
        is_primary=True,
    )
    partner.status = Partner.Status.ACTIVE
    partner.save(update_fields=["status", "updated_at"])
    return partner


@pytest.mark.django_db
def test_pilot_readiness_command_reports_configured_partner_without_token(
    pilot_ready_partner,
) -> None:
    output = StringIO()

    call_command(
        "check_pilot_readiness",
        partner_id=str(pilot_ready_partner.id),
        stdout=output,
    )

    rendered = output.getvalue()
    assert "Partner is ready for the Telegram pilot." in rendered
    assert "[OK] primary master Telegram contact: bound" in rendered
    assert "encrypted-value" not in rendered


@pytest.mark.django_db
def test_pilot_readiness_command_fails_with_actionable_missing_contact(
    pilot_ready_partner,
) -> None:
    PartnerTelegramContact.objects.filter(partner=pilot_ready_partner).delete()
    output = StringIO()

    with pytest.raises(CommandError, match="not ready"):
        call_command(
            "check_pilot_readiness",
            partner_id=str(pilot_ready_partner.id),
            stdout=output,
        )

    assert "[MISSING] primary master Telegram contact: open a signed admin link" in (
        output.getvalue()
    )


def test_broadcast_task_uses_conservative_pilot_batch(monkeypatch) -> None:
    observed = {}

    def fake_dispatch(*, deliver, limit):
        observed["deliver"] = deliver
        observed["limit"] = limit
        return BroadcastDispatchSummary(sent=1)

    monkeypatch.setattr(
        "apps.broadcasts.tasks.dispatch_due_broadcast_recipients",
        fake_dispatch,
    )

    assert dispatch_due_recipients_task() == 1
    assert observed["limit"] == 1


def test_beat_schedule_routes_urgent_and_bulk_work_to_separate_queues(settings) -> None:
    schedule = settings.CELERY_BEAT_SCHEDULE

    assert schedule["dispatch-due-notifications"]["options"]["queue"] == "notifications"
    assert schedule["dispatch-broadcast-recipient"]["options"]["queue"] == "broadcasts"
    assert schedule["cleanup-expired-retained-records"]["options"]["queue"] == "maintenance"
