import asyncio
from datetime import datetime, time, timedelta
from datetime import timezone as datetime_timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.appointments.services import (
    AppointmentServiceItemInput,
    create_confirmed_appointment,
    create_requested_appointment,
)
from apps.bot.master_workspace import (
    PERIOD_REQUESTS,
    PERIOD_TODAY,
    PERIOD_TOMORROW,
    build_master_appointment_detail_reply,
    build_master_appointment_list_reply,
    build_master_workspace_reply,
    master_workspace_reply_from_callback_data,
    set_master_language_reply,
)
from apps.bot.models import PartnerTelegramContact
from apps.bot.routers import build_partner_router
from apps.catalog.services import create_service, create_service_category
from apps.clients.services import (
    resolve_client_profile_for_telegram_identity,
    upsert_telegram_identity,
)
from apps.partners.services import create_partner_with_owner
from apps.schedule.selectors import default_booking_resource_for_partner
from core.timezones import get_zone


@pytest.fixture
def master_workspace_context():
    superuser = User.objects.create_superuser(
        username="master-workspace-root",
        password="secret",
    )
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Master Workspace",
        owner_username="master-workspace-owner",
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
        price_amount=50_000,
        duration_minutes=60,
    )
    master_identity = upsert_telegram_identity(
        telegram_user_id=771001,
        username="workspace-master",
        first_name="Master",
    )
    contact = PartnerTelegramContact.objects.create(
        partner=created.partner,
        telegram_identity=master_identity,
        user=created.owner,
        is_primary=True,
    )
    return {
        "superuser": superuser,
        "partner": created.partner,
        "owner": created.owner,
        "resource": default_booking_resource_for_partner(created.partner),
        "service": service,
        "contact": contact,
    }


def _client(context, suffix: int):
    identity = upsert_telegram_identity(
        telegram_user_id=772000 + suffix,
        username=f"workspace-client-{suffix}",
        first_name=f"Client {suffix}",
    )
    return resolve_client_profile_for_telegram_identity(
        partner=context["partner"],
        telegram_identity=identity,
    )


def _local_time(context, *, day_offset: int, hour: int) -> datetime:
    zone = get_zone(context["partner"].timezone)
    local_date = timezone.now().astimezone(zone).date() + timedelta(days=day_offset)
    return datetime.combine(local_date, time(hour=hour), tzinfo=zone).astimezone(
        datetime_timezone.utc
    )


def _request(context, *, suffix: int, start_at: datetime):
    return create_requested_appointment(
        actor=None,
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=_client(context, suffix),
        start_at=start_at,
        service_items=[AppointmentServiceItemInput(service=context["service"])],
        client_comment="Please confirm.",
    )


def _callbacks(reply):
    return [
        button.callback_data
        for row in reply.reply_markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


@pytest.mark.django_db
def test_master_workspace_shows_operational_counts(master_workspace_context):
    context = master_workspace_context
    now = _local_time(context, day_offset=0, hour=8)
    create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=_client(context, 1),
        start_at=_local_time(context, day_offset=0, hour=10),
        service_items=[AppointmentServiceItemInput(service=context["service"])],
    )
    _request(
        context,
        suffix=2,
        start_at=_local_time(context, day_offset=1, hour=12),
    )

    reply = build_master_workspace_reply(
        partner=context["partner"],
        actor=context["owner"],
        now=now,
    )

    assert "Робочий кабінет Master Workspace" in reply.text
    assert "Заявки: 1" in reply.text
    assert "сьогодні: 1" in reply.text
    assert "завтра: 1" in reply.text
    assert _callbacks(reply) == [
        "mw:list:r:1",
        "mw:list:d0:1",
        "mw:list:d1:1",
        "mw:list:u:1",
        "mw:lang",
    ]


@pytest.mark.django_db
def test_master_request_list_is_paginated_and_details_keep_actions(
    master_workspace_context,
):
    context = master_workspace_context
    start_at = _local_time(context, day_offset=2, hour=9)
    appointments = [
        _request(
            context,
            suffix=index,
            start_at=start_at + timedelta(minutes=index * 5),
        )
        for index in range(1, 7)
    ]

    first_page = build_master_appointment_list_reply(
        partner=context["partner"],
        actor=context["owner"],
        period=PERIOD_REQUESTS,
        page=1,
    )
    second_page = build_master_appointment_list_reply(
        partner=context["partner"],
        actor=context["owner"],
        period=PERIOD_REQUESTS,
        page=2,
    )
    details = build_master_appointment_detail_reply(
        partner=context["partner"],
        actor=context["owner"],
        appointment_id=appointments[0].id,
        return_period=PERIOD_REQUESTS,
    )

    assert "Заявки: 6" in first_page.text
    assert (
        len(
            [
                callback
                for callback in _callbacks(first_page)
                if callback.startswith("mw:view")
            ]
        )
        == 5
    )
    assert "mw:list:r:2" in _callbacks(first_page)
    assert (
        len(
            [
                callback
                for callback in _callbacks(second_page)
                if callback.startswith("mw:view")
            ]
        )
        == 1
    )
    assert f"mr:confirm:{appointments[0].id.hex}" in _callbacks(details)
    assert f"mr:decline:{appointments[0].id.hex}" in _callbacks(details)
    assert f"mr:propose:{appointments[0].id.hex}" in _callbacks(details)
    assert "mw:list:r:1" in _callbacks(details)
    assert all(len(callback.encode("utf-8")) <= 64 for callback in _callbacks(details))


@pytest.mark.django_db
def test_master_workspace_day_lists_use_partner_timezone(master_workspace_context):
    context = master_workspace_context
    now = _local_time(context, day_offset=0, hour=8)
    today = _request(
        context,
        suffix=20,
        start_at=_local_time(context, day_offset=0, hour=21),
    )
    tomorrow = _request(
        context,
        suffix=21,
        start_at=_local_time(context, day_offset=1, hour=9),
    )

    today_reply = build_master_appointment_list_reply(
        partner=context["partner"],
        actor=context["owner"],
        period=PERIOD_TODAY,
        now=now,
    )
    tomorrow_reply = build_master_appointment_list_reply(
        partner=context["partner"],
        actor=context["owner"],
        period=PERIOD_TOMORROW,
        now=now,
    )

    assert any(today.id.hex in callback for callback in _callbacks(today_reply))
    assert not any(tomorrow.id.hex in callback for callback in _callbacks(today_reply))
    assert any(tomorrow.id.hex in callback for callback in _callbacks(tomorrow_reply))


@pytest.mark.django_db
def test_master_can_change_workspace_language(master_workspace_context):
    context = master_workspace_context

    reply = set_master_language_reply(
        partner=context["partner"],
        actor=context["owner"],
        language="ru",
    )

    context["partner"].refresh_from_db()
    assert context["partner"].default_language == "ru"
    assert "Язык мастера изменён: Русский" in reply.text
    assert "Рабочий кабинет Master Workspace" in reply.text
    assert "mw:lang" in _callbacks(reply)


@pytest.mark.django_db
def test_master_workspace_cannot_open_foreign_appointment(master_workspace_context):
    context = master_workspace_context
    other = create_partner_with_owner(
        actor=context["superuser"],
        partner_name="Other Workspace",
        owner_username="other-workspace-owner",
        owner_password="secret",
    )
    appointment = _request(
        context,
        suffix=30,
        start_at=_local_time(context, day_offset=3, hour=11),
    )

    with pytest.raises(ValidationError):
        master_workspace_reply_from_callback_data(
            partner=other.partner,
            actor=other.owner,
            data=f"mw:view:{appointment.id.hex}:r:1",
        )


@pytest.mark.parametrize("start_payload", [None, "non_admin_deep_link"])
def test_linked_master_start_opens_workspace_before_client_flow(
    monkeypatch,
    start_payload,
):
    partner = SimpleNamespace(default_language="uk")
    actor = SimpleNamespace()
    contact = SimpleNamespace(user=actor)
    markup = SimpleNamespace(inline_keyboard=[])
    message = SimpleNamespace(
        from_user=SimpleNamespace(
            id=771001,
            username="master",
            first_name="Master",
            last_name="",
            is_premium=False,
            language_code="uk",
        ),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(clear=AsyncMock())
    master_resolver = Mock(return_value=contact)
    reachable = Mock()

    monkeypatch.setattr(
        "apps.bot.routers.master_telegram_contact_for_user",
        master_resolver,
    )
    monkeypatch.setattr("apps.bot.routers.mark_master_contact_reachable", reachable)
    monkeypatch.setattr(
        "apps.bot.routers.build_master_workspace_reply",
        lambda **_kwargs: SimpleNamespace(text="Кабінет майстра", reply_markup=markup),
    )
    monkeypatch.setattr(
        "apps.bot.routers.handle_start_command",
        lambda **_kwargs: pytest.fail("linked master must not enter client start flow"),
    )
    router = build_partner_router()
    start_handler = next(
        handler.callback
        for handler in router.message.handlers
        if handler.callback.__name__ == "start_handler"
    )

    asyncio.run(
        start_handler(
            message=message,
            command=SimpleNamespace(args=start_payload),
            partner=partner,
            state=state,
        )
    )

    state.clear.assert_awaited_once()
    master_resolver.assert_called_once_with(partner=partner, telegram_user_id=771001)
    reachable.assert_called_once_with(contact=contact)
    message.answer.assert_awaited_once_with("Кабінет майстра", reply_markup=markup)
