from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.appointments.models import Appointment
from apps.bot.booking import (
    build_booking_categories_reply,
    build_booking_dates_reply,
    build_booking_final_confirm_reply,
    build_booking_services_reply,
    build_booking_slots_reply,
    build_booking_summary_reply,
    confirm_booking_reply,
)
from apps.catalog.services import create_service, create_service_category
from apps.clients.services import (
    resolve_client_profile_for_telegram_identity,
    upsert_telegram_identity,
)
from apps.partners.services import create_partner_with_owner
from apps.schedule.models import PartnerScheduleSettings
from apps.schedule.selectors import default_booking_resource_for_partner
from apps.schedule.services import create_availability_window, update_schedule_settings


@pytest.fixture
def bot_booking_context():
    superuser = User.objects.create_superuser(username="bot-booking-root", password="secret")
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Bot Booking Partner",
        owner_username="bot-booking-owner",
        owner_password="secret",
    )
    category = create_service_category(
        actor=created.owner,
        partner=created.partner,
        name="Nails",
    )
    service = create_service(
        actor=created.owner,
        partner=created.partner,
        category=category,
        name="Manicure",
        price_amount=50000,
        duration_minutes=60,
    )
    identity = upsert_telegram_identity(
        telegram_user_id=777001,
        username="client",
        first_name="Ira",
        language_code="en",
    )
    client = resolve_client_profile_for_telegram_identity(
        partner=created.partner,
        telegram_identity=identity,
    )
    return {
        "partner": created.partner,
        "owner": created.owner,
        "resource": default_booking_resource_for_partner(created.partner),
        "category": category,
        "service": service,
        "client": client,
    }


@pytest.fixture
def bot_booking_start():
    return (timezone.now() + timedelta(days=4)).replace(
        hour=9,
        minute=0,
        second=0,
        microsecond=0,
    )


def _open_window(context, start_at, *, hours=2):
    create_availability_window(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        start_at=start_at,
        end_at=start_at + timedelta(hours=hours),
    )


def _first_callback(reply):
    return reply.reply_markup.inline_keyboard[0][0].callback_data


@pytest.mark.django_db
def test_bot_booking_reply_builders_walk_single_service_flow(
    bot_booking_context,
    bot_booking_start,
):
    context = bot_booking_context
    update_schedule_settings(
        actor=context["owner"],
        partner=context["partner"],
        confirmation_mode=PartnerScheduleSettings.ConfirmationMode.AUTO,
        slot_step_minutes=30,
    )
    _open_window(context, bot_booking_start)
    now = bot_booking_start - timedelta(hours=1)

    categories = build_booking_categories_reply(partner=context["partner"], language="en")
    services = build_booking_services_reply(
        partner=context["partner"],
        category_id=context["category"].id,
        language="en",
    )
    summary = build_booking_summary_reply(
        partner=context["partner"],
        service_id=context["service"].id,
        language="en",
    )
    dates = build_booking_dates_reply(
        partner=context["partner"],
        service_id=context["service"].id,
        language="en",
        now=now,
    )
    slots = build_booking_slots_reply(
        partner=context["partner"],
        service_id=context["service"].id,
        local_date=bot_booking_start.date(),
        language="en",
        now=now,
    )
    final = build_booking_final_confirm_reply(
        partner=context["partner"],
        service_id=context["service"].id,
        start_at=bot_booking_start,
        language="en",
        now=now,
    )

    assert "Choose a service category" in categories.text
    assert _first_callback(categories) == f"bk:svc:{context['category'].id.hex}:1"
    assert "Manicure" in services.reply_markup.inline_keyboard[0][0].text
    assert _first_callback(services) == f"bk:sum:{context['service'].id.hex}"
    assert "Your selection" in summary.text
    assert _first_callback(summary) == f"bk:dates:{context['service'].id.hex}:1"
    assert "Choose a date" in dates.text
    assert _first_callback(dates).startswith(f"bk:slots:{context['service'].id.hex}:")
    assert _first_callback(dates).endswith(":1")
    assert "Choose a time" in slots.text
    assert _first_callback(slots).startswith(f"bk:final:{context['service'].id.hex}:")
    assert "Confirm booking" in final.text
    assert _first_callback(final).startswith(f"bk:create:{context['service'].id.hex}:")


@pytest.mark.django_db
def test_confirm_booking_reply_creates_confirmed_appointment(
    bot_booking_context,
    bot_booking_start,
):
    context = bot_booking_context
    update_schedule_settings(
        actor=context["owner"],
        partner=context["partner"],
        confirmation_mode=PartnerScheduleSettings.ConfirmationMode.AUTO,
    )
    _open_window(context, bot_booking_start)

    reply = confirm_booking_reply(
        partner=context["partner"],
        client_profile=context["client"],
        service_id=context["service"].id,
        start_at=bot_booking_start,
        language="en",
        now=bot_booking_start - timedelta(hours=1),
    )

    appointment = Appointment.objects.get(partner=context["partner"])
    assert appointment.status == Appointment.Status.CONFIRMED
    assert appointment.service_items.count() == 1
    assert "confirmed" in reply.text


@pytest.mark.django_db
def test_bot_booking_adapter_rejects_cross_partner_service(bot_booking_context):
    context = bot_booking_context
    superuser = User.objects.create_superuser(username="other-root", password="secret")
    other = create_partner_with_owner(
        actor=superuser,
        partner_name="Other Partner",
        owner_username="other-owner",
        owner_password="secret",
    )
    other_category = create_service_category(
        actor=other.owner,
        partner=other.partner,
        name="Other",
    )
    other_service = create_service(
        actor=other.owner,
        partner=other.partner,
        category=other_category,
        name="Other service",
        price_amount=10000,
        duration_minutes=30,
    )

    with pytest.raises(ValidationError):
        build_booking_summary_reply(
            partner=context["partner"],
            service_id=other_service.id,
            language="en",
        )
