import asyncio
from datetime import datetime, time, timedelta
from datetime import timezone as datetime_timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.appointments.models import Appointment
from apps.appointments.services import (
    AppointmentServiceItemInput,
    create_confirmed_appointment,
    create_requested_appointment,
)
from apps.bot.master_workspace import (
    MASTER_WORKSPACE_INPUT_LATE_ARRIVAL,
    PERIOD_REQUESTS,
    PERIOD_TODAY,
    PERIOD_TOMORROW,
    build_master_appointment_detail_reply,
    build_master_appointment_list_reply,
    build_master_workspace_reply,
    create_master_late_arrival_penalty_reply,
    master_workspace_reply_from_callback_data,
    queue_master_appointment_message_reply,
    set_master_language_reply,
)
from apps.bot.models import PartnerTelegramContact
from apps.bot.routers import (
    MasterWorkspaceLateArrivalFlowState,
    MasterWorkspaceMessageFlowState,
    build_partner_router,
)
from apps.catalog.services import create_service, create_service_category
from apps.clients.services import (
    resolve_client_profile_for_telegram_identity,
    upsert_telegram_identity,
)
from apps.finance.models import Penalty
from apps.finance.services import update_penalty_settings
from apps.messaging.models import ConversationMessage
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
        len([callback for callback in _callbacks(first_page) if callback.startswith("mw:view")])
        == 5
    )
    assert "mw:list:r:2" in _callbacks(first_page)
    assert (
        len([callback for callback in _callbacks(second_page) if callback.startswith("mw:view")])
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


@pytest.mark.django_db
def test_master_workspace_status_actions_require_confirmation_and_use_owner_service(
    master_workspace_context,
):
    context = master_workspace_context
    now = timezone.now()
    appointment = create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=_client(context, 40),
        start_at=now - timedelta(hours=2),
        service_items=[AppointmentServiceItemInput(service=context["service"])],
    )

    details = build_master_appointment_detail_reply(
        partner=context["partner"],
        actor=context["owner"],
        appointment_id=appointment.id,
        return_period=PERIOD_TODAY,
        now=now,
    )

    assert f"mw:status:{appointment.id.hex}:c:d0:1" in _callbacks(details)
    assert f"mw:status:{appointment.id.hex}:n:d0:1" in _callbacks(details)
    assert f"mw:status:{appointment.id.hex}:x:d0:1" in _callbacks(details)
    assert f"mw:message:{appointment.id.hex}:d0:1" in _callbacks(details)
    assert all(len(callback.encode("utf-8")) <= 64 for callback in _callbacks(details))

    confirmation = master_workspace_reply_from_callback_data(
        partner=context["partner"],
        actor=context["owner"],
        data=f"mw:status:{appointment.id.hex}:c:d0:1",
        now=now,
    )
    appointment.refresh_from_db()
    assert appointment.status == Appointment.Status.CONFIRMED
    assert f"mw:statusgo:{appointment.id.hex}:c:d0:1" in _callbacks(confirmation)

    completed = master_workspace_reply_from_callback_data(
        partner=context["partner"],
        actor=context["owner"],
        data=f"mw:statusgo:{appointment.id.hex}:c:d0:1",
    )
    appointment.refresh_from_db()
    assert appointment.status == Appointment.Status.COMPLETED
    assert "Статус запису змінено: завершено" in completed.text
    assert not any(callback.startswith("mw:status:") for callback in _callbacks(completed))


@pytest.mark.django_db
def test_master_workspace_suggests_and_creates_enabled_no_show_penalty(
    master_workspace_context,
):
    context = master_workspace_context
    update_penalty_settings(
        actor=context["owner"],
        partner=context["partner"],
        penalties_enabled=True,
    )
    appointment = create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=_client(context, 41),
        start_at=timezone.now() - timedelta(hours=2),
        service_items=[AppointmentServiceItemInput(service=context["service"])],
    )
    no_show = master_workspace_reply_from_callback_data(
        partner=context["partner"],
        actor=context["owner"],
        data=f"mw:statusgo:{appointment.id.hex}:n:d0:1",
    )

    assert f"mw:penalty:{appointment.id.hex}:d0:1" in _callbacks(no_show)
    confirmation = master_workspace_reply_from_callback_data(
        partner=context["partner"],
        actor=context["owner"],
        data=f"mw:penalty:{appointment.id.hex}:d0:1",
    )
    assert "50.00 UAH" in confirmation.text
    assert not Penalty.objects.filter(source_appointment=appointment).exists()

    created = master_workspace_reply_from_callback_data(
        partner=context["partner"],
        actor=context["owner"],
        data=f"mw:penaltygo:{appointment.id.hex}:d0:1",
    )

    penalty = Penalty.objects.get(source_appointment=appointment)
    assert penalty.reason == Penalty.Reason.NO_SHOW
    assert penalty.amount == 5000
    assert "Штраф за неявку створено: 50.00 UAH" in created.text
    assert not any(callback.startswith("mw:penalty:") for callback in _callbacks(created))


@pytest.mark.django_db
def test_master_workspace_records_enabled_late_arrival_penalty(
    master_workspace_context,
):
    context = master_workspace_context
    update_penalty_settings(
        actor=context["owner"],
        partner=context["partner"],
        penalties_enabled=True,
        late_arrival_threshold_minutes=15,
        late_arrival_penalty_amount=2_000,
    )
    now = timezone.now()
    appointment = create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=_client(context, 43),
        start_at=now - timedelta(hours=1),
        service_items=[AppointmentServiceItemInput(service=context["service"])],
    )

    details = build_master_appointment_detail_reply(
        partner=context["partner"],
        actor=context["owner"],
        appointment_id=appointment.id,
        return_period=PERIOD_TODAY,
        now=now,
    )
    late_callback = f"mw:late:{appointment.id.hex}:d0:1"
    assert late_callback in _callbacks(details)

    prompt = master_workspace_reply_from_callback_data(
        partner=context["partner"],
        actor=context["owner"],
        data=late_callback,
        now=now,
    )
    assert prompt.wait_for_text is True
    assert prompt.input_mode == MASTER_WORKSPACE_INPUT_LATE_ARRIVAL
    assert "20.00 UAH" in prompt.text
    assert "15" in prompt.text

    created = create_master_late_arrival_penalty_reply(
        partner=context["partner"],
        actor=context["owner"],
        appointment_id=appointment.id,
        return_period=PERIOD_TODAY,
        text="25 затор на дорозі",
    )

    penalty = Penalty.objects.get(
        source_appointment=appointment,
        reason=Penalty.Reason.LATE_ARRIVAL,
    )
    assert penalty.amount == 2_000
    assert penalty.late_by_minutes == 25
    assert penalty.note == "затор на дорозі"
    assert "Запізнення 25 хв зафіксовано" in created.text
    assert late_callback not in _callbacks(created)


@pytest.mark.django_db
def test_master_workspace_rejects_lateness_below_partner_threshold(
    master_workspace_context,
):
    context = master_workspace_context
    update_penalty_settings(
        actor=context["owner"],
        partner=context["partner"],
        penalties_enabled=True,
        late_arrival_threshold_minutes=20,
    )
    appointment = create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=_client(context, 44),
        start_at=timezone.now() - timedelta(hours=1),
        service_items=[AppointmentServiceItemInput(service=context["service"])],
    )

    with pytest.raises(ValidationError):
        create_master_late_arrival_penalty_reply(
            partner=context["partner"],
            actor=context["owner"],
            appointment_id=appointment.id,
            return_period=PERIOD_TODAY,
            text="15",
        )

    assert not Penalty.objects.filter(
        source_appointment=appointment,
        reason=Penalty.Reason.LATE_ARRIVAL,
    ).exists()


@pytest.mark.django_db
def test_master_workspace_queues_direct_message_for_appointment_client(
    master_workspace_context,
):
    context = master_workspace_context
    appointment = create_confirmed_appointment(
        actor=context["owner"],
        partner=context["partner"],
        booking_resource=context["resource"],
        client_profile=_client(context, 42),
        start_at=timezone.now() + timedelta(days=2),
        service_items=[AppointmentServiceItemInput(service=context["service"])],
    )

    prompt = master_workspace_reply_from_callback_data(
        partner=context["partner"],
        actor=context["owner"],
        data=f"mw:message:{appointment.id.hex}:u:2",
    )

    assert prompt.wait_for_text is True
    assert prompt.appointment_id == appointment.id
    assert prompt.return_period == "u"
    assert prompt.return_page == 2
    queued = queue_master_appointment_message_reply(
        partner=context["partner"],
        actor=context["owner"],
        appointment_id=appointment.id,
        return_period="u",
        return_page=2,
        text="See you soon.",
    )

    message = ConversationMessage.objects.get(
        partner=context["partner"],
        client_profile=appointment.client_profile,
        direction=ConversationMessage.Direction.MASTER_TO_CLIENT,
    )
    assert message.text == "See you soon."
    assert message.delivery_status == ConversationMessage.DeliveryStatus.QUEUED
    assert "поставлено в чергу доставки" in queued.text
    assert "mw:list:u:2" in _callbacks(queued)


def test_master_workspace_message_callback_enters_text_input_state(monkeypatch):
    partner = SimpleNamespace(default_language="uk")
    actor = SimpleNamespace()
    contact = SimpleNamespace(user=actor)
    appointment_id = "5fd8e2f7632849358d40b8fb6af777cd"
    reply = SimpleNamespace(
        text="Напишіть повідомлення",
        reply_markup=SimpleNamespace(inline_keyboard=[]),
        wait_for_text=True,
        appointment_id=UUID(appointment_id),
        return_period="u",
        return_page=2,
    )
    callback = SimpleNamespace(
        data=f"mw:message:{appointment_id}:u:2",
        from_user=SimpleNamespace(id=771001),
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(
        clear=AsyncMock(),
        set_state=AsyncMock(),
        set_data=AsyncMock(),
    )

    monkeypatch.setattr(
        "apps.bot.routers.master_telegram_contact_for_user",
        lambda **_kwargs: contact,
    )
    monkeypatch.setattr(
        "apps.bot.routers.master_workspace_reply_from_callback_data",
        lambda **_kwargs: reply,
    )
    router = build_partner_router()
    handler = next(
        handler.callback
        for handler in router.callback_query.handlers
        if handler.callback.__name__ == "master_workspace_callback_handler"
    )

    asyncio.run(handler(callback=callback, partner=partner, state=state))

    state.clear.assert_awaited_once()
    state.set_state.assert_awaited_once_with(MasterWorkspaceMessageFlowState.waiting_for_text)
    state.set_data.assert_awaited_once_with(
        {
            "master_workspace_message_appointment_id": appointment_id,
            "master_workspace_message_period": "u",
            "master_workspace_message_page": 2,
        }
    )
    callback.message.edit_text.assert_awaited_once_with(
        reply.text,
        reply_markup=reply.reply_markup,
    )


def test_master_workspace_late_arrival_callback_enters_own_input_state(monkeypatch):
    partner = SimpleNamespace(default_language="uk")
    actor = SimpleNamespace()
    contact = SimpleNamespace(user=actor)
    appointment_id = "5fd8e2f7632849358d40b8fb6af777cd"
    reply = SimpleNamespace(
        text="Вкажіть хвилини",
        reply_markup=SimpleNamespace(inline_keyboard=[]),
        wait_for_text=True,
        appointment_id=UUID(appointment_id),
        return_period="d0",
        return_page=1,
        input_mode=MASTER_WORKSPACE_INPUT_LATE_ARRIVAL,
    )
    callback = SimpleNamespace(
        data=f"mw:late:{appointment_id}:d0:1",
        from_user=SimpleNamespace(id=771001),
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )
    state = SimpleNamespace(
        clear=AsyncMock(),
        set_state=AsyncMock(),
        set_data=AsyncMock(),
    )

    monkeypatch.setattr(
        "apps.bot.routers.master_telegram_contact_for_user",
        lambda **_kwargs: contact,
    )
    monkeypatch.setattr(
        "apps.bot.routers.master_workspace_reply_from_callback_data",
        lambda **_kwargs: reply,
    )
    router = build_partner_router()
    handler = next(
        handler.callback
        for handler in router.callback_query.handlers
        if handler.callback.__name__ == "master_workspace_callback_handler"
    )

    asyncio.run(handler(callback=callback, partner=partner, state=state))

    state.clear.assert_awaited_once()
    state.set_state.assert_awaited_once_with(
        MasterWorkspaceLateArrivalFlowState.waiting_for_text
    )
    state.set_data.assert_awaited_once_with(
        {
            "master_workspace_message_appointment_id": appointment_id,
            "master_workspace_message_period": "d0",
            "master_workspace_message_page": 1,
        }
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
