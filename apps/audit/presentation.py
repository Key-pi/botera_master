from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.utils.dateparse import parse_date, parse_datetime
from django.utils.translation import gettext_lazy as _

from core.money import Money
from core.timezones import to_partner_time

if TYPE_CHECKING:
    from apps.audit.models import AuditEvent


@dataclass(frozen=True, slots=True)
class ActivityPresentation:
    """Business-facing title and category for one audit event type."""

    title: str
    category: str


@dataclass(frozen=True, slots=True)
class ActivityTargetPresentation:
    """Known admin destination for an audit target without resolving the object."""

    admin_url_name: str
    label: str


ACTIVITY_CATEGORY_CHOICES = (
    ("appointments", _("Appointments")),
    ("calendar", _("Calendar")),
    ("penalties", _("Penalties")),
    ("expenses", _("Expenses")),
    ("broadcasts", _("Broadcasts")),
    ("messages", _("Messages")),
    ("notifications", _("Notifications")),
    ("settings", _("Bot and settings")),
    ("clients_catalog", _("Clients and catalog")),
    ("other", _("Other")),
)
ACTIVITY_CATEGORY_LABELS = dict(ACTIVITY_CATEGORY_CHOICES)
ACTIVITY_CATEGORY_PREFIXES = {
    "appointments": ("appointment.",),
    "calendar": ("schedule.",),
    "penalties": ("penalty.",),
    "expenses": ("expense.",),
    "broadcasts": ("broadcast.",),
    "messages": ("messaging.",),
    "notifications": ("notification.", "reminder."),
    "settings": ("bot.", "settings.", "partner."),
    "clients_catalog": ("client.", "catalog.", "service.", "category."),
}

EVENT_PRESENTATIONS = {
    "appointment.created": ActivityPresentation(_("Appointment created"), "appointments"),
    "appointment.status_changed": ActivityPresentation(
        _("Appointment status changed"),
        "appointments",
    ),
    "appointment.status_corrected": ActivityPresentation(
        _("Final appointment status corrected"),
        "appointments",
    ),
    "appointment.proposal_created": ActivityPresentation(
        _("Appointment change proposed"),
        "appointments",
    ),
    "appointment.proposal_accepted": ActivityPresentation(
        _("Appointment change accepted"),
        "appointments",
    ),
    "appointment.proposal_declined": ActivityPresentation(
        _("Appointment change declined"),
        "appointments",
    ),
    "appointment.proposal_cancelled": ActivityPresentation(
        _("Appointment change proposal cancelled"),
        "appointments",
    ),
    "appointment.proposal_expired": ActivityPresentation(
        _("Appointment change proposal expired"),
        "appointments",
    ),
    "appointment.client_reschedule_declined": ActivityPresentation(
        _("Client reschedule request declined"),
        "appointments",
    ),
    "appointment.client_selected_alternative_time": ActivityPresentation(
        _("Client selected another time"),
        "appointments",
    ),
    "appointment.client_rescheduled": ActivityPresentation(
        _("Client rescheduled appointment"),
        "appointments",
    ),
    "schedule.conflicts_kept_as_is": ActivityPresentation(
        _("Schedule conflicts kept as is"),
        "calendar",
    ),
    "schedule.conflicts_reopened": ActivityPresentation(
        _("Schedule conflicts reopened"),
        "calendar",
    ),
    "schedule.conflict_reschedules_requested": ActivityPresentation(
        _("Conflict reschedules requested"),
        "calendar",
    ),
    "schedule.settings_updated": ActivityPresentation(
        _("Booking settings updated"),
        "calendar",
    ),
    "penalty.settings_updated": ActivityPresentation(
        _("Penalty settings updated"),
        "penalties",
    ),
    "penalty.created": ActivityPresentation(_("Penalty created"), "penalties"),
    "penalty.applied": ActivityPresentation(_("Penalty applied to appointment"), "penalties"),
    "penalty.paid": ActivityPresentation(_("Penalty marked paid"), "penalties"),
    "penalty.released": ActivityPresentation(_("Penalty released from appointment"), "penalties"),
    "penalty.waived": ActivityPresentation(_("Penalty waived"), "penalties"),
    "expense.created": ActivityPresentation(_("Expense recorded"), "expenses"),
    "expense.updated": ActivityPresentation(_("Expense updated"), "expenses"),
    "expense.deleted": ActivityPresentation(_("Expense deleted"), "expenses"),
    "broadcast.created": ActivityPresentation(_("Broadcast created"), "broadcasts"),
    "broadcast.updated": ActivityPresentation(_("Broadcast updated"), "broadcasts"),
    "broadcast.scheduled": ActivityPresentation(_("Broadcast scheduled"), "broadcasts"),
    "broadcast.started": ActivityPresentation(_("Broadcast started"), "broadcasts"),
    "broadcast.cancelled": ActivityPresentation(_("Broadcast cancelled"), "broadcasts"),
    "broadcast.completed": ActivityPresentation(_("Broadcast completed"), "broadcasts"),
    "broadcast.failed": ActivityPresentation(
        _("Broadcast completed with failures"),
        "broadcasts",
    ),
    "broadcast.template_created": ActivityPresentation(
        _("Broadcast template created"),
        "broadcasts",
    ),
    "broadcast.template_updated": ActivityPresentation(
        _("Broadcast template updated"),
        "broadcasts",
    ),
    "broadcast.template_deleted": ActivityPresentation(
        _("Broadcast template deleted"),
        "broadcasts",
    ),
    "messaging.message_created": ActivityPresentation(
        _("Client-master message created"),
        "messages",
    ),
    "notification.sent": ActivityPresentation(_("Notification sent"), "notifications"),
    "notification.failed": ActivityPresentation(_("Notification delivery failed"), "notifications"),
    "notification.settings_updated": ActivityPresentation(
        _("Notification settings updated"),
        "notifications",
    ),
    "notification.reminder_rules_updated": ActivityPresentation(
        _("Appointment reminder rules updated"),
        "notifications",
    ),
    "reminder.sent": ActivityPresentation(_("Reminder sent"), "notifications"),
    "reminder.failed": ActivityPresentation(_("Reminder delivery failed"), "notifications"),
    "bot.token_updated": ActivityPresentation(_("Bot token updated"), "settings"),
    "bot.status_changed": ActivityPresentation(_("Bot status changed"), "settings"),
    "bot.connection_checked": ActivityPresentation(_("Bot connection checked"), "settings"),
    "bot.master_contact_link_created": ActivityPresentation(
        _("Master Telegram connection link created"),
        "settings",
    ),
    "bot.content_override_saved": ActivityPresentation(
        _("Bot content customized"),
        "settings",
    ),
    "bot.content_override_reset": ActivityPresentation(
        _("Bot content reset to default"),
        "settings",
    ),
    "bot.settings_updated": ActivityPresentation(_("Bot settings updated"), "settings"),
    "partner.settings_updated": ActivityPresentation(_("Partner settings updated"), "settings"),
    "client.created": ActivityPresentation(_("Client created"), "clients_catalog"),
    "client.updated": ActivityPresentation(_("Client updated"), "clients_catalog"),
    "catalog.service_created": ActivityPresentation(_("Service created"), "clients_catalog"),
    "catalog.service_updated": ActivityPresentation(_("Service updated"), "clients_catalog"),
}

TARGET_PRESENTATIONS = {
    "Appointment": ActivityTargetPresentation(
        "admin:appointments_appointment_change",
        _("Appointment"),
    ),
    "AppointmentProposal": ActivityTargetPresentation(
        "admin:appointments_appointmentproposal_change",
        _("Appointment proposal"),
    ),
    "Penalty": ActivityTargetPresentation("admin:finance_penalty_change", _("Penalty")),
    "PartnerPenaltySettings": ActivityTargetPresentation(
        "admin:finance_partnerpenaltysettings_change",
        _("Penalty settings"),
    ),
    "Expense": ActivityTargetPresentation("admin:finance_expense_change", _("Expense")),
    "BroadcastCampaign": ActivityTargetPresentation(
        "admin:broadcasts_broadcastcampaign_change",
        _("Broadcast"),
    ),
    "BroadcastTemplate": ActivityTargetPresentation(
        "admin:broadcasts_broadcasttemplate_change",
        _("Broadcast template"),
    ),
    "ConversationMessage": ActivityTargetPresentation(
        "admin:messaging_conversationmessage_change",
        _("Message"),
    ),
    "ClientProfile": ActivityTargetPresentation(
        "admin:clients_clientprofile_change",
        _("Client"),
    ),
    "BotInstance": ActivityTargetPresentation("admin:bot_botinstance_change", _("Bot")),
    "PartnerTemplateOverride": ActivityTargetPresentation(
        "admin:bot_partnertemplateoverride_change",
        _("Bot content"),
    ),
    "PartnerNotificationSettings": ActivityTargetPresentation(
        "admin:notifications_partnernotificationsettings_change",
        _("Notification settings"),
    ),
    "PartnerScheduleSettings": ActivityTargetPresentation(
        "admin:schedule_partnerschedulesettings_change",
        _("Schedule settings"),
    ),
    "Service": ActivityTargetPresentation("admin:catalog_service_change", _("Service")),
    "ServiceCategory": ActivityTargetPresentation(
        "admin:catalog_servicecategory_change",
        _("Service category"),
    ),
    "Partner": ActivityTargetPresentation("admin:partners_partner_change", _("Partner")),
}

CLIENT_ACTOR_EVENT_TYPES = {
    "appointment.client_rescheduled",
    "appointment.client_selected_alternative_time",
    "appointment.proposal_accepted",
    "appointment.proposal_created",
    "appointment.proposal_declined",
}

DETAIL_FIELDS = (
    ("old_status", _("Previous status"), "choice"),
    ("new_status", _("New status"), "choice"),
    ("result_status", _("Resulting status"), "choice"),
    ("status", _("Status"), "choice"),
    ("source", _("Source"), "choice"),
    ("proposal_type", _("Proposal type"), "choice"),
    ("direction", _("Direction"), "choice"),
    ("start_at", _("Appointment time"), "datetime"),
    ("previous_start_at", _("Previous time"), "datetime"),
    ("new_start_at", _("New time"), "datetime"),
    ("expires_at", _("Expires"), "datetime"),
    ("scheduled_at", _("Scheduled for"), "datetime"),
    ("date", _("Date"), "date"),
    ("service_count", _("Services"), "number"),
    ("option_count", _("Options"), "number"),
    ("count", _("Affected records"), "number"),
    ("requested_count", _("Reschedule requests"), "number"),
    ("skipped_without_telegram_count", _("Skipped without Telegram"), "number"),
    ("queued_count", _("Pending recipients"), "number"),
    ("sent_count", _("Sent"), "number"),
    ("failed_count", _("Failed"), "number"),
    ("skipped_count", _("Skipped"), "number"),
    ("is_near_fit", _("Near-fit slot"), "boolean"),
    ("success", _("Successful"), "boolean"),
    ("token_changed", _("Token changed"), "boolean"),
    ("template_key", _("Content key"), "text"),
    ("language", _("Language"), "choice"),
    ("appointment_cancelled", _("Appointment cancelled"), "boolean"),
    ("penalties_enabled", _("Penalties enabled"), "boolean"),
    ("late_arrival_threshold_minutes", _("Late-arrival threshold"), "minutes"),
    ("amount", _("Amount"), "money"),
    ("total_amount", _("Total"), "money"),
    ("late_cancel_penalty_amount", _("Late-cancellation penalty"), "money"),
    ("no_show_penalty_amount", _("No-show penalty"), "money"),
    ("late_arrival_penalty_amount", _("Late-arrival penalty"), "money"),
    ("reason", _("Penalty reason"), "choice"),
)
BROADCAST_COUNTER_KEYS = {"queued_count", "sent_count", "failed_count", "skipped_count"}
CHOICE_LABELS = {
    "active": _("Active"),
    "appointment_change": _("Appointment change"),
    "applied_to_appointment": _("Applied to appointment"),
    "auto": _("Auto-confirm"),
    "cancelled": _("Cancelled"),
    "cancelled_by_client": _("Cancelled by client"),
    "cancelled_by_master": _("Cancelled by master"),
    "client_bot": _("Client bot"),
    "client_to_master": _("Client to master"),
    "completed": _("Completed"),
    "confirmed": _("Confirmed"),
    "en": _("English"),
    "error": _("Error"),
    "failed": _("Failed"),
    "late_arrival": _("Late arrival"),
    "late_cancel": _("Late cancellation"),
    "manual": _("Manual confirmation"),
    "manual_admin": _("Manual admin"),
    "master_to_client": _("Master to client"),
    "no_show": _("No-show"),
    "paid": _("Paid"),
    "paused": _("Paused"),
    "queued": _("Queued"),
    "requested": _("Requested"),
    "requested_alternatives": _("Requested alternatives"),
    "reschedule_confirmed": _("Confirmed appointment reschedule"),
    "ru": _("Russian"),
    "sending": _("Sending"),
    "sent": _("Sent"),
    "setup_required": _("Setup required"),
    "skipped": _("Skipped"),
    "suspended": _("Suspended"),
    "uk": _("Ukrainian"),
    "unpaid": _("Unpaid"),
    "waived": _("Waived"),
}


def activity_title(event_type: str) -> str:
    presentation = EVENT_PRESENTATIONS.get(event_type)
    if presentation is not None:
        return presentation.title
    return _humanize_identifier(event_type.rsplit(".", 1)[-1]) or _("Activity recorded")


def activity_category(event_type: str) -> str:
    presentation = EVENT_PRESENTATIONS.get(event_type)
    if presentation is not None:
        return presentation.category
    for category, prefixes in ACTIVITY_CATEGORY_PREFIXES.items():
        if event_type.startswith(prefixes):
            return category
    return "other"


def activity_category_label(event_type: str) -> str:
    return ACTIVITY_CATEGORY_LABELS[activity_category(event_type)]


def activity_actor_label(event: AuditEvent) -> str:
    if event.actor_id:
        full_name = event.actor.get_full_name().strip()
        return full_name or event.actor.get_username()

    payload = event.payload_masked if isinstance(event.payload_masked, dict) else {}
    if event.event_type in CLIENT_ACTOR_EVENT_TYPES:
        return _("Client")
    if (
        event.event_type == "messaging.message_created"
        and payload.get("direction") == "client_to_master"
    ):
        return _("Client")
    if (
        event.event_type == "appointment.status_changed"
        and payload.get("new_status") == "cancelled_by_client"
    ):
        return _("Client")
    if event.event_type == "appointment.created" and payload.get("source") == "client_bot":
        return _("Client")
    return _("System")


def activity_detail_rows(event: AuditEvent) -> tuple[tuple[str, str], ...]:
    """Return a finite business-safe view of the masked payload."""

    payload = event.payload_masked if isinstance(event.payload_masked, dict) else {}
    if not payload:
        return ()
    suppress_empty_broadcast_counters = not any(
        _non_zero(payload.get(key)) for key in BROADCAST_COUNTER_KEYS
    )
    rows: list[tuple[str, str]] = []
    for key, label, value_type in DETAIL_FIELDS:
        if key not in payload or payload[key] in (None, ""):
            continue
        if key in BROADCAST_COUNTER_KEYS and suppress_empty_broadcast_counters:
            continue
        rows.append(
            (
                label,
                _format_detail_value(
                    value=payload[key],
                    value_type=value_type,
                    currency=str(payload.get("currency") or "UAH"),
                    timezone_name=event.partner.timezone,
                ),
            )
        )
    return tuple(rows)


def activity_target_presentation(event: AuditEvent) -> ActivityTargetPresentation | None:
    return TARGET_PRESENTATIONS.get(event.target_type)


def _format_detail_value(
    *,
    value: Any,
    value_type: str,
    currency: str,
    timezone_name: str,
) -> str:
    if value_type == "boolean":
        return _("Yes") if value is True else _("No")
    if value_type == "choice":
        return _humanize_identifier(str(value))
    if value_type == "datetime":
        parsed = parse_datetime(str(value))
        if parsed is not None and parsed.tzinfo is not None:
            return to_partner_time(parsed, timezone_name).strftime("%Y-%m-%d %H:%M %Z")
        return str(value)
    if value_type == "date":
        parsed = parse_date(str(value))
        return parsed.isoformat() if parsed is not None else str(value)
    if value_type == "money":
        try:
            money = Money(amount=int(value), currency=currency)
        except (TypeError, ValueError):
            return str(value)
        return f"{money.to_major_decimal()} {money.currency}"
    if value_type == "minutes":
        return _("%(count)s min") % {"count": value}
    return str(value)


def _humanize_identifier(value: str) -> str:
    known_label = CHOICE_LABELS.get(value)
    if known_label is not None:
        return known_label
    cleaned = value.replace("_", " ").strip()
    return cleaned[:1].upper() + cleaned[1:] if cleaned else ""


def _non_zero(value: Any) -> bool:
    try:
        return int(value) != 0
    except (TypeError, ValueError):
        return bool(value)
