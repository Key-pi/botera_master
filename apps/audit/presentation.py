from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.utils.dateparse import parse_date, parse_datetime

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
    ("appointments", "Appointments"),
    ("calendar", "Calendar"),
    ("penalties", "Penalties"),
    ("expenses", "Expenses"),
    ("broadcasts", "Broadcasts"),
    ("messages", "Messages"),
    ("notifications", "Notifications"),
    ("settings", "Bot and settings"),
    ("clients_catalog", "Clients and catalog"),
    ("other", "Other"),
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
    "appointment.created": ActivityPresentation("Appointment created", "appointments"),
    "appointment.status_changed": ActivityPresentation(
        "Appointment status changed",
        "appointments",
    ),
    "appointment.status_corrected": ActivityPresentation(
        "Final appointment status corrected",
        "appointments",
    ),
    "appointment.proposal_created": ActivityPresentation(
        "Appointment change proposed",
        "appointments",
    ),
    "appointment.proposal_accepted": ActivityPresentation(
        "Appointment change accepted",
        "appointments",
    ),
    "appointment.proposal_declined": ActivityPresentation(
        "Appointment change declined",
        "appointments",
    ),
    "appointment.proposal_cancelled": ActivityPresentation(
        "Appointment change proposal cancelled",
        "appointments",
    ),
    "appointment.proposal_expired": ActivityPresentation(
        "Appointment change proposal expired",
        "appointments",
    ),
    "appointment.client_reschedule_declined": ActivityPresentation(
        "Client reschedule request declined",
        "appointments",
    ),
    "appointment.client_selected_alternative_time": ActivityPresentation(
        "Client selected another time",
        "appointments",
    ),
    "appointment.client_rescheduled": ActivityPresentation(
        "Client rescheduled appointment",
        "appointments",
    ),
    "schedule.conflicts_kept_as_is": ActivityPresentation(
        "Schedule conflicts kept as is",
        "calendar",
    ),
    "schedule.conflicts_reopened": ActivityPresentation(
        "Schedule conflicts reopened",
        "calendar",
    ),
    "schedule.conflict_reschedules_requested": ActivityPresentation(
        "Conflict reschedules requested",
        "calendar",
    ),
    "penalty.settings_updated": ActivityPresentation(
        "Penalty settings updated",
        "penalties",
    ),
    "penalty.created": ActivityPresentation("Penalty created", "penalties"),
    "penalty.applied": ActivityPresentation("Penalty applied to appointment", "penalties"),
    "penalty.paid": ActivityPresentation("Penalty marked paid", "penalties"),
    "penalty.released": ActivityPresentation("Penalty released from appointment", "penalties"),
    "penalty.waived": ActivityPresentation("Penalty waived", "penalties"),
    "expense.created": ActivityPresentation("Expense recorded", "expenses"),
    "expense.updated": ActivityPresentation("Expense updated", "expenses"),
    "expense.deleted": ActivityPresentation("Expense deleted", "expenses"),
    "broadcast.created": ActivityPresentation("Broadcast created", "broadcasts"),
    "broadcast.updated": ActivityPresentation("Broadcast updated", "broadcasts"),
    "broadcast.scheduled": ActivityPresentation("Broadcast scheduled", "broadcasts"),
    "broadcast.started": ActivityPresentation("Broadcast started", "broadcasts"),
    "broadcast.cancelled": ActivityPresentation("Broadcast cancelled", "broadcasts"),
    "broadcast.completed": ActivityPresentation("Broadcast completed", "broadcasts"),
    "broadcast.failed": ActivityPresentation(
        "Broadcast completed with failures",
        "broadcasts",
    ),
    "broadcast.template_created": ActivityPresentation(
        "Broadcast template created",
        "broadcasts",
    ),
    "broadcast.template_updated": ActivityPresentation(
        "Broadcast template updated",
        "broadcasts",
    ),
    "broadcast.template_deleted": ActivityPresentation(
        "Broadcast template deleted",
        "broadcasts",
    ),
    "messaging.message_created": ActivityPresentation(
        "Client-master message created",
        "messages",
    ),
    "notification.sent": ActivityPresentation("Notification sent", "notifications"),
    "notification.failed": ActivityPresentation("Notification delivery failed", "notifications"),
    "reminder.sent": ActivityPresentation("Reminder sent", "notifications"),
    "reminder.failed": ActivityPresentation("Reminder delivery failed", "notifications"),
    "bot.token_updated": ActivityPresentation("Bot token updated", "settings"),
    "bot.status_changed": ActivityPresentation("Bot status changed", "settings"),
    "bot.connection_checked": ActivityPresentation("Bot connection checked", "settings"),
    "bot.master_contact_link_created": ActivityPresentation(
        "Master Telegram connection link created",
        "settings",
    ),
    "bot.content_override_saved": ActivityPresentation(
        "Bot content customized",
        "settings",
    ),
    "bot.content_override_reset": ActivityPresentation(
        "Bot content reset to default",
        "settings",
    ),
    "bot.settings_updated": ActivityPresentation("Bot settings updated", "settings"),
    "client.created": ActivityPresentation("Client created", "clients_catalog"),
    "client.updated": ActivityPresentation("Client updated", "clients_catalog"),
    "catalog.service_created": ActivityPresentation("Service created", "clients_catalog"),
    "catalog.service_updated": ActivityPresentation("Service updated", "clients_catalog"),
}

TARGET_PRESENTATIONS = {
    "Appointment": ActivityTargetPresentation(
        "admin:appointments_appointment_change",
        "Appointment",
    ),
    "AppointmentProposal": ActivityTargetPresentation(
        "admin:appointments_appointmentproposal_change",
        "Appointment proposal",
    ),
    "Penalty": ActivityTargetPresentation("admin:finance_penalty_change", "Penalty"),
    "PartnerPenaltySettings": ActivityTargetPresentation(
        "admin:finance_partnerpenaltysettings_change",
        "Penalty settings",
    ),
    "Expense": ActivityTargetPresentation("admin:finance_expense_change", "Expense"),
    "BroadcastCampaign": ActivityTargetPresentation(
        "admin:broadcasts_broadcastcampaign_change",
        "Broadcast",
    ),
    "BroadcastTemplate": ActivityTargetPresentation(
        "admin:broadcasts_broadcasttemplate_change",
        "Broadcast template",
    ),
    "ConversationMessage": ActivityTargetPresentation(
        "admin:messaging_conversationmessage_change",
        "Message",
    ),
    "ClientProfile": ActivityTargetPresentation(
        "admin:clients_clientprofile_change",
        "Client",
    ),
    "BotInstance": ActivityTargetPresentation("admin:bot_botinstance_change", "Bot"),
    "PartnerTemplateOverride": ActivityTargetPresentation(
        "admin:bot_partnertemplateoverride_change",
        "Bot content",
    ),
    "PartnerNotificationSettings": ActivityTargetPresentation(
        "admin:notifications_partnernotificationsettings_change",
        "Notification settings",
    ),
    "PartnerScheduleSettings": ActivityTargetPresentation(
        "admin:schedule_partnerschedulesettings_change",
        "Schedule settings",
    ),
    "Service": ActivityTargetPresentation("admin:catalog_service_change", "Service"),
    "ServiceCategory": ActivityTargetPresentation(
        "admin:catalog_servicecategory_change",
        "Service category",
    ),
    "Partner": ActivityTargetPresentation("admin:partners_partner_change", "Partner"),
}

CLIENT_ACTOR_EVENT_TYPES = {
    "appointment.client_rescheduled",
    "appointment.client_selected_alternative_time",
    "appointment.proposal_accepted",
    "appointment.proposal_created",
    "appointment.proposal_declined",
}

DETAIL_FIELDS = (
    ("old_status", "Previous status", "choice"),
    ("new_status", "New status", "choice"),
    ("result_status", "Resulting status", "choice"),
    ("status", "Status", "choice"),
    ("source", "Source", "choice"),
    ("proposal_type", "Proposal type", "choice"),
    ("direction", "Direction", "choice"),
    ("start_at", "Appointment time", "datetime"),
    ("previous_start_at", "Previous time", "datetime"),
    ("new_start_at", "New time", "datetime"),
    ("expires_at", "Expires", "datetime"),
    ("scheduled_at", "Scheduled for", "datetime"),
    ("date", "Date", "date"),
    ("service_count", "Services", "number"),
    ("option_count", "Options", "number"),
    ("count", "Affected records", "number"),
    ("requested_count", "Reschedule requests", "number"),
    ("skipped_without_telegram_count", "Skipped without Telegram", "number"),
    ("queued_count", "Pending recipients", "number"),
    ("sent_count", "Sent", "number"),
    ("failed_count", "Failed", "number"),
    ("skipped_count", "Skipped", "number"),
    ("is_near_fit", "Near-fit slot", "boolean"),
    ("success", "Successful", "boolean"),
    ("token_changed", "Token changed", "boolean"),
    ("template_key", "Content key", "text"),
    ("language", "Language", "choice"),
    ("appointment_cancelled", "Appointment cancelled", "boolean"),
    ("penalties_enabled", "Penalties enabled", "boolean"),
    ("late_arrival_threshold_minutes", "Late-arrival threshold", "minutes"),
    ("amount", "Amount", "money"),
    ("total_amount", "Total", "money"),
    ("late_cancel_penalty_amount", "Late-cancellation penalty", "money"),
    ("no_show_penalty_amount", "No-show penalty", "money"),
    ("late_arrival_penalty_amount", "Late-arrival penalty", "money"),
    ("reason", "Penalty reason", "choice"),
)
BROADCAST_COUNTER_KEYS = {"queued_count", "sent_count", "failed_count", "skipped_count"}


def activity_title(event_type: str) -> str:
    presentation = EVENT_PRESENTATIONS.get(event_type)
    if presentation is not None:
        return presentation.title
    return _humanize_identifier(event_type.rsplit(".", 1)[-1]) or "Activity recorded"


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
        return "Client"
    if (
        event.event_type == "messaging.message_created"
        and payload.get("direction") == "client_to_master"
    ):
        return "Client"
    if (
        event.event_type == "appointment.status_changed"
        and payload.get("new_status") == "cancelled_by_client"
    ):
        return "Client"
    if event.event_type == "appointment.created" and payload.get("source") == "client_bot":
        return "Client"
    return "System"


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
        return "Yes" if value is True else "No"
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
        return f"{value} min"
    return str(value)


def _humanize_identifier(value: str) -> str:
    cleaned = value.replace("_", " ").strip()
    return cleaned[:1].upper() + cleaned[1:] if cleaned else ""


def _non_zero(value: Any) -> bool:
    try:
        return int(value) != 0
    except (TypeError, ValueError):
        return bool(value)
