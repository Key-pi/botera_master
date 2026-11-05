from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from datetime import timezone as datetime_timezone
from math import ceil
from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.services import ensure_partner_access
from apps.appointments.models import Appointment
from apps.appointments.selectors import (
    appointment_for_partner_by_id,
    appointments_for_partner,
    appointments_for_partner_period,
    requested_appointments_for_partner,
)
from apps.bot.master import master_booking_request_markup
from apps.partners.models import SUPPORTED_PARTNER_LANGUAGES, Partner
from apps.partners.selectors import partner_dashboard_snapshot
from apps.partners.services import update_partner_settings
from core.money import Money
from core.timezones import get_zone, to_partner_time

from .services import render_content_template

MASTER_WORKSPACE_CALLBACK_PREFIX = "mw"
MASTER_WORKSPACE_PAGE_SIZE = 5

PERIOD_REQUESTS = "r"
PERIOD_TODAY = "d0"
PERIOD_TOMORROW = "d1"
PERIOD_UPCOMING = "u"
MASTER_WORKSPACE_PERIODS = frozenset(
    {
        PERIOD_REQUESTS,
        PERIOD_TODAY,
        PERIOD_TOMORROW,
        PERIOD_UPCOMING,
    }
)


@dataclass(frozen=True, slots=True)
class BotMasterWorkspaceReply:
    """Text and keyboard for the linked master's Telegram workspace."""

    text: str
    reply_markup: InlineKeyboardMarkup | None = None


@dataclass(frozen=True, slots=True)
class MasterAppointmentPage:
    """Bounded appointment page without loading the whole partner history."""

    items: tuple[Appointment, ...]
    page: int
    total_items: int
    total_pages: int

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages

    @property
    def indicator(self) -> str:
        return f"{self.page}/{self.total_pages}"


def build_master_workspace_reply(
    *,
    partner: Partner,
    actor: User,
    now: datetime | None = None,
) -> BotMasterWorkspaceReply:
    """Render the operational landing screen for a verified master contact."""

    ensure_partner_access(actor, partner)
    snapshot = partner_dashboard_snapshot(partner=partner, now=now)
    language = partner.default_language
    text = _render(
        partner=partner,
        key="master.workspace_main",
        language=language,
        context={
            "partner_name": partner.name,
            "requested_count": str(snapshot.pending_request_count),
            "today_count": str(snapshot.today_appointment_count),
            "tomorrow_count": str(snapshot.tomorrow_appointment_count),
            "failed_count": str(snapshot.failed_notification_count),
            "conflict_count": str(snapshot.schedule_conflict_count),
        },
    )
    return BotMasterWorkspaceReply(
        text=text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"{_label('requests', language)} ({snapshot.pending_request_count})",
                        callback_data=_list_callback(PERIOD_REQUESTS, 1),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=f"{_label('today', language)} ({snapshot.today_appointment_count})",
                        callback_data=_list_callback(PERIOD_TODAY, 1),
                    ),
                    InlineKeyboardButton(
                        text=(
                            f"{_label('tomorrow', language)} "
                            f"({snapshot.tomorrow_appointment_count})"
                        ),
                        callback_data=_list_callback(PERIOD_TOMORROW, 1),
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text=_label("upcoming", language),
                        callback_data=_list_callback(PERIOD_UPCOMING, 1),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=_label("language", language),
                        callback_data=_callback("lang"),
                    )
                ],
            ]
        ),
    )


def build_master_appointment_list_reply(
    *,
    partner: Partner,
    actor: User,
    period: str,
    page: int = 1,
    now: datetime | None = None,
) -> BotMasterWorkspaceReply:
    """Render one paginated operational appointment list."""

    ensure_partner_access(actor, partner)
    _validate_period(period)
    current_time = now or timezone.now()
    appointments_page = _appointment_page(
        partner=partner,
        period=period,
        page=page,
        now=current_time,
    )
    language = partner.default_language
    period_label = _period_label(period, language)
    key = "master.workspace_list" if appointments_page.items else "master.workspace_empty"
    text = _render(
        partner=partner,
        key=key,
        language=language,
        context={
            "period": period_label,
            "count": str(appointments_page.total_items),
        },
    )
    rows = [
        [
            InlineKeyboardButton(
                text=_appointment_button_label(appointment, language=language),
                callback_data=_view_callback(
                    appointment_id=appointment.id,
                    period=period,
                    page=appointments_page.page,
                ),
            )
        ]
        for appointment in appointments_page.items
    ]
    rows.extend(_pagination_rows(period=period, page=appointments_page, language=language))
    rows.append([_main_button(language)])
    return BotMasterWorkspaceReply(
        text=text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def build_master_appointment_detail_reply(
    *,
    partner: Partner,
    actor: User,
    appointment_id: UUID,
    return_period: str,
    return_page: int = 1,
) -> BotMasterWorkspaceReply:
    """Render a tenant-scoped appointment and its currently safe actions."""

    ensure_partner_access(actor, partner)
    _validate_period(return_period)
    appointment = appointment_for_partner_by_id(
        partner=partner,
        appointment_id=appointment_id,
    )
    if appointment is None:
        raise ValidationError({"appointment": "Appointment was not found."})

    language = partner.default_language
    local_start = to_partner_time(appointment.start_at, partner.timezone)
    local_end = to_partner_time(appointment.end_at, partner.timezone)
    comment = appointment.client_comment.strip()
    comment_block = (
        f"\n{_label('comment', language)}: {comment}" if comment else ""
    )
    text = _render(
        partner=partner,
        key="master.workspace_appointment_details",
        language=language,
        context={
            "client_name": appointment.client_profile.display_name,
            "client_phone": appointment.client_profile.phone or _label("not_set", language),
            "status": _status_label(appointment.status, language),
            "date": local_start.date().isoformat(),
            "start_time": local_start.strftime("%H:%M"),
            "end_time": local_end.strftime("%H:%M"),
            "services": _services_label(appointment),
            "total": str(
                Money(
                    amount=appointment.total_amount,
                    currency=appointment.currency,
                ).to_major_decimal()
            ),
            "currency": appointment.currency,
            "comment": comment_block,
        },
    )
    rows: list[list[InlineKeyboardButton]] = []
    if appointment.status == Appointment.Status.REQUESTED:
        rows.extend(
            master_booking_request_markup(
                appointment_id=appointment.id,
                language=language,
            ).inline_keyboard
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=_label("back", language),
                callback_data=_list_callback(return_period, return_page),
            )
        ]
    )
    rows.append([_main_button(language)])
    return BotMasterWorkspaceReply(
        text=text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def build_master_language_reply(
    *,
    partner: Partner,
    actor: User,
) -> BotMasterWorkspaceReply:
    """Render the language picker for a verified partner owner."""

    ensure_partner_access(actor, partner)
    language = partner.default_language
    rows = [
        [
            InlineKeyboardButton(
                text=_language_label(choice),
                callback_data=_callback("langset", choice),
            )
        ]
        for choice in ("uk", "ru", "en")
        if choice in SUPPORTED_PARTNER_LANGUAGES
    ]
    rows.append([_main_button(language)])
    return BotMasterWorkspaceReply(
        text=_render(
            partner=partner,
            key="master.workspace_choose_language",
            language=language,
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


def set_master_language_reply(
    *,
    partner: Partner,
    actor: User,
    language: str,
    now: datetime | None = None,
) -> BotMasterWorkspaceReply:
    """Persist the partner/master bot language through the owning service."""

    cleaned_language = language.strip().lower()
    if cleaned_language not in SUPPORTED_PARTNER_LANGUAGES:
        raise ValidationError({"language": "Unsupported partner language."})
    updated_partner = update_partner_settings(
        actor=actor,
        partner=partner,
        default_language=cleaned_language,
    )
    workspace = build_master_workspace_reply(
        partner=updated_partner,
        actor=actor,
        now=now,
    )
    confirmation = _render(
        partner=updated_partner,
        key="master.workspace_language_updated",
        language=cleaned_language,
        context={"language": _language_label(cleaned_language)},
    )
    return BotMasterWorkspaceReply(
        text=f"{confirmation}\n\n{workspace.text}",
        reply_markup=workspace.reply_markup,
    )


def master_workspace_reply_from_callback_data(
    *,
    partner: Partner,
    actor: User,
    data: str,
    now: datetime | None = None,
) -> BotMasterWorkspaceReply:
    """Resolve one compact callback only after trusted master identification."""

    parts = data.split(":")
    if not parts or parts[0] != MASTER_WORKSPACE_CALLBACK_PREFIX:
        raise ValidationError({"callback": "Unsupported callback."})
    if parts == [MASTER_WORKSPACE_CALLBACK_PREFIX, "main"]:
        return build_master_workspace_reply(partner=partner, actor=actor, now=now)
    if parts == [MASTER_WORKSPACE_CALLBACK_PREFIX, "lang"]:
        return build_master_language_reply(partner=partner, actor=actor)
    if len(parts) == 3 and parts[1] == "langset":
        return set_master_language_reply(
            partner=partner,
            actor=actor,
            language=parts[2],
            now=now,
        )
    if len(parts) == 4 and parts[1] == "list":
        return build_master_appointment_list_reply(
            partner=partner,
            actor=actor,
            period=parts[2],
            page=_parse_page(parts[3]),
            now=now,
        )
    if len(parts) == 5 and parts[1] == "view":
        return build_master_appointment_detail_reply(
            partner=partner,
            actor=actor,
            appointment_id=_parse_uuid(parts[2]),
            return_period=parts[3],
            return_page=_parse_page(parts[4]),
        )
    raise ValidationError({"callback": "Unsupported callback."})


def _appointment_page(
    *,
    partner: Partner,
    period: str,
    page: int,
    now: datetime,
) -> MasterAppointmentPage:
    queryset = _appointments_for_period(
        partner=partner,
        period=period,
        now=now,
    )
    total_items = queryset.count()
    total_pages = max(1, ceil(total_items / MASTER_WORKSPACE_PAGE_SIZE))
    current_page = min(max(page, 1), total_pages)
    start = (current_page - 1) * MASTER_WORKSPACE_PAGE_SIZE
    end = start + MASTER_WORKSPACE_PAGE_SIZE
    return MasterAppointmentPage(
        items=tuple(queryset[start:end]),
        page=current_page,
        total_items=total_items,
        total_pages=total_pages,
    )


def _appointments_for_period(*, partner: Partner, period: str, now: datetime):
    if period == PERIOD_REQUESTS:
        return requested_appointments_for_partner(partner=partner)
    if period == PERIOD_UPCOMING:
        return (
            appointments_for_partner(partner)
            .filter(
                status__in=(Appointment.Status.REQUESTED, Appointment.Status.CONFIRMED),
                start_at__gte=now,
            )
            .prefetch_related("service_items")
            .order_by("start_at", "created_at")
        )

    zone = get_zone(partner.timezone)
    local_today = now.astimezone(zone).date()
    local_date = local_today if period == PERIOD_TODAY else local_today + timedelta(days=1)
    start_at = datetime.combine(local_date, time.min, tzinfo=zone).astimezone(
        datetime_timezone.utc
    )
    end_at = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo=zone).astimezone(
        datetime_timezone.utc
    )
    return appointments_for_partner_period(
        partner=partner,
        start_at=start_at,
        end_at=end_at,
        statuses=(
            Appointment.Status.REQUESTED,
            Appointment.Status.CONFIRMED,
            Appointment.Status.COMPLETED,
            Appointment.Status.NO_SHOW,
        ),
    )


def _pagination_rows(
    *,
    period: str,
    page: MasterAppointmentPage,
    language: str,
) -> list[list[InlineKeyboardButton]]:
    if page.total_pages <= 1:
        return []
    row: list[InlineKeyboardButton] = []
    if page.has_previous:
        row.append(
            InlineKeyboardButton(
                text=_label("previous", language),
                callback_data=_list_callback(period, page.page - 1),
            )
        )
    row.append(InlineKeyboardButton(text=page.indicator, callback_data="mw:noop"))
    if page.has_next:
        row.append(
            InlineKeyboardButton(
                text=_label("next", language),
                callback_data=_list_callback(period, page.page + 1),
            )
        )
    return [row]


def _appointment_button_label(appointment: Appointment, *, language: str) -> str:
    local_start = to_partner_time(appointment.start_at, appointment.partner.timezone)
    client_name = appointment.client_profile.display_name.strip() or _label("client", language)
    if len(client_name) > 28:
        client_name = f"{client_name[:27]}…"
    marker = _status_marker(appointment.status)
    return f"{marker} {local_start:%d.%m %H:%M} · {client_name}"


def _services_label(appointment: Appointment) -> str:
    names = [item.service_name_snapshot for item in appointment.service_items.all()]
    return ", ".join(names)


def _status_marker(status: str) -> str:
    return {
        Appointment.Status.REQUESTED: "?",
        Appointment.Status.CONFIRMED: "+",
        Appointment.Status.COMPLETED: "✓",
        Appointment.Status.NO_SHOW: "!",
    }.get(status, "·")


def _status_label(status: str, language: str) -> str:
    labels = {
        Appointment.Status.REQUESTED: {
            "uk": "очікує підтвердження",
            "ru": "ожидает подтверждения",
            "en": "awaiting confirmation",
        },
        Appointment.Status.CONFIRMED: {
            "uk": "підтверджено",
            "ru": "подтверждено",
            "en": "confirmed",
        },
        Appointment.Status.CANCELLED_BY_CLIENT: {
            "uk": "скасовано клієнтом",
            "ru": "отменено клиентом",
            "en": "cancelled by client",
        },
        Appointment.Status.CANCELLED_BY_MASTER: {
            "uk": "скасовано майстром",
            "ru": "отменено мастером",
            "en": "cancelled by master",
        },
        Appointment.Status.COMPLETED: {
            "uk": "завершено",
            "ru": "завершено",
            "en": "completed",
        },
        Appointment.Status.NO_SHOW: {
            "uk": "клієнт не прийшов",
            "ru": "клиент не пришёл",
            "en": "no-show",
        },
    }
    translations = labels.get(status)
    if translations is None:
        return status
    return translations.get(language, translations["uk"])


def _period_label(period: str, language: str) -> str:
    return _label(
        {
            PERIOD_REQUESTS: "requests",
            PERIOD_TODAY: "today",
            PERIOD_TOMORROW: "tomorrow",
            PERIOD_UPCOMING: "upcoming",
        }[period],
        language,
    )


def _main_button(language: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=_label("main", language),
        callback_data=_callback("main"),
    )


def _list_callback(period: str, page: int) -> str:
    return _callback("list", period, page)


def _view_callback(*, appointment_id: UUID, period: str, page: int) -> str:
    return _callback("view", appointment_id.hex, period, page)


def _callback(action: str, *parts: object) -> str:
    values = (MASTER_WORKSPACE_CALLBACK_PREFIX, action, *(str(part) for part in parts))
    return ":".join(values)


def _parse_page(value: str) -> int:
    try:
        page = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"page": "Invalid page."}) from exc
    if page < 1:
        raise ValidationError({"page": "Invalid page."})
    return page


def _parse_uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"appointment": "Invalid appointment."}) from exc


def _validate_period(period: str) -> None:
    if period not in MASTER_WORKSPACE_PERIODS:
        raise ValidationError({"period": "Unsupported appointment period."})


def _language_label(language: str) -> str:
    return {
        "uk": "Українська",
        "ru": "Русский",
        "en": "English",
    }.get(language, language.upper())


def _render(
    *,
    partner: Partner,
    key: str,
    language: str,
    context: dict[str, str] | None = None,
) -> str:
    return render_content_template(
        partner=partner,
        key=key,
        language=language,
        context=context,
    ).body


def _label(key: str, language: str) -> str:
    labels = {
        "requests": {"uk": "Заявки", "ru": "Заявки", "en": "Requests"},
        "today": {"uk": "Сьогодні", "ru": "Сегодня", "en": "Today"},
        "tomorrow": {"uk": "Завтра", "ru": "Завтра", "en": "Tomorrow"},
        "upcoming": {"uk": "Найближчі", "ru": "Ближайшие", "en": "Upcoming"},
        "language": {"uk": "Мова", "ru": "Язык", "en": "Language"},
        "main": {"uk": "Головна", "ru": "Главная", "en": "Home"},
        "back": {"uk": "Назад", "ru": "Назад", "en": "Back"},
        "previous": {"uk": "←", "ru": "←", "en": "←"},
        "next": {"uk": "→", "ru": "→", "en": "→"},
        "comment": {"uk": "Коментар", "ru": "Комментарий", "en": "Comment"},
        "not_set": {"uk": "не вказано", "ru": "не указано", "en": "not set"},
        "client": {"uk": "Клієнт", "ru": "Клиент", "en": "Client"},
    }
    translations = labels[key]
    return translations.get(language, translations["uk"])
