from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode
from uuid import UUID

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse

from apps.bot.content import SYSTEM_TEMPLATE_DEFINITIONS
from apps.bot.forms import (
    BOT_WORKSPACE_ACTION_CHECK_CONNECTION,
    BOT_WORKSPACE_ACTION_CREATE_MASTER_LINK,
    BOT_WORKSPACE_ACTION_LAUNCH,
    BOT_WORKSPACE_ACTION_PAUSE,
    BotTokenWorkspaceForm,
    BotWorkspaceActionForm,
)
from apps.bot.models import BotInstance
from apps.bot.selectors import (
    bot_instance_for_partner,
    primary_master_telegram_contact,
    template_overrides_for_partner,
)
from apps.bot.services import (
    activate_partner_bot,
    check_bot_connection,
    create_master_contact_link,
    set_bot_status,
    set_bot_token,
)
from apps.finance.selectors import penalty_settings_for_partner
from apps.notifications.selectors import (
    notification_settings_for_partner,
    reminder_rules_for_partner,
)
from apps.partners.selectors import visible_partners_for_user
from apps.partners.setup import SetupStep, get_partner_setup_status
from apps.schedule.selectors import schedule_settings_for_partner

BOT_WORKSPACE_DEFAULT_TAB = "instance"
BOT_WORKSPACE_TAB_LABELS = {
    "instance": "Instance",
    "settings": "Settings",
    "content": "Content",
    "notifications": "Notifications",
    "booking": "Booking",
    "penalties": "Penalties",
}
CONTENT_GROUP_LABELS = {
    "start": "Start and welcome",
    "booking": "Booking",
    "appointment": "Appointments",
    "master": "Master notifications",
    "reminder": "Reminders",
    "profile": "Client profile",
    "penalty": "Penalties",
    "messaging": "Messaging",
    "broadcast": "Broadcast buttons",
}


@dataclass(frozen=True, slots=True)
class BotWorkspaceTab:
    slug: str
    label: str
    url: str


@staff_member_required
def workspace(request):
    """Render one tenant-safe operational surface for partner bot configuration."""

    partners = tuple(visible_partners_for_user(request.user).order_by("name", "id"))
    selected_partner = _selected_partner(request=request, partners=partners)
    selected_tab = _selected_tab(request)
    generated_master_link = None
    token_form = (
        BotTokenWorkspaceForm(partner=selected_partner) if selected_partner is not None else None
    )

    if request.method == "POST":
        if selected_partner is None:
            raise Http404("Partner not found.")
        action = request.POST.get("action", "").strip()
        if action == "replace_token":
            token_form = BotTokenWorkspaceForm(request.POST, partner=selected_partner)
            if token_form.is_valid():
                set_bot_token(
                    actor=request.user,
                    partner=selected_partner,
                    raw_token=token_form.cleaned_data["new_token"],
                )
                messages.success(request, "Bot token saved securely. Check the connection next.")
                return HttpResponseRedirect(
                    _workspace_url(partner_id=selected_partner.id, tab="instance")
                )
        else:
            action_form = BotWorkspaceActionForm(request.POST)
            if not action_form.is_valid():
                messages.error(request, "Unknown bot action.")
            else:
                try:
                    generated_master_link = _perform_instance_action(
                        actor=request.user,
                        partner=selected_partner,
                        action=action_form.cleaned_data["action"],
                    )
                except (PermissionDenied, ValidationError) as exc:
                    messages.warning(request, _error_text(exc))
                else:
                    if generated_master_link is None:
                        messages.success(request, _instance_action_success(action))
                        return HttpResponseRedirect(
                            _workspace_url(partner_id=selected_partner.id, tab="instance")
                        )

    context = {
        **admin.site.each_context(request),
        "is_nav_sidebar_enabled": False,
        "title": "Bot",
        "partners": partners,
        "selected_partner": selected_partner,
        "selected_tab": selected_tab,
        "token_form": token_form,
        "generated_master_link": generated_master_link,
    }
    if selected_partner is not None:
        context.update(
            _workspace_context(
                partner=selected_partner,
            )
        )
    return TemplateResponse(request, "bot/workspace.html", context)


def _perform_instance_action(*, actor, partner, action: str):
    bot_instance = bot_instance_for_partner(partner)
    if bot_instance is None:
        raise ValidationError("Save a bot token before using this action.")

    if action == BOT_WORKSPACE_ACTION_CHECK_CONNECTION:
        checked = check_bot_connection(bot_instance, actor=actor)
        if checked.last_error_code:
            raise ValidationError(
                checked.last_error_message_masked
                or "Telegram did not accept the current bot configuration."
            )
        return None
    if action == BOT_WORKSPACE_ACTION_LAUNCH:
        activate_partner_bot(actor=actor, bot_instance=bot_instance)
        return None
    if action == BOT_WORKSPACE_ACTION_PAUSE:
        set_bot_status(
            actor=actor,
            bot_instance=bot_instance,
            status=BotInstance.Status.PAUSED,
        )
        return None
    if action == BOT_WORKSPACE_ACTION_CREATE_MASTER_LINK:
        return create_master_contact_link(actor=actor, partner=partner)
    raise ValidationError("Unknown bot action.")


def _workspace_context(*, partner) -> dict:
    bot_instance = bot_instance_for_partner(partner)
    setup_status = get_partner_setup_status(partner)
    primary_contact = primary_master_telegram_contact(partner)
    notification_settings = notification_settings_for_partner(partner=partner)
    reminder_rules = tuple(reminder_rules_for_partner(partner=partner))
    schedule_settings = schedule_settings_for_partner(partner)
    penalty_settings = penalty_settings_for_partner(partner=partner)
    overrides = tuple(template_overrides_for_partner(partner))

    return {
        "bot_instance": bot_instance,
        "setup_status": setup_status,
        "setup_steps": _setup_steps(
            setup_status=setup_status,
            bot_instance=bot_instance,
            primary_contact=primary_contact,
            partner=partner,
        ),
        "primary_contact": primary_contact,
        "notification_settings": notification_settings,
        "reminder_rules": reminder_rules,
        "schedule_settings": schedule_settings,
        "penalty_settings": penalty_settings,
        "content_groups": _content_groups(overrides=overrides),
        "content_override_count": len(overrides),
        "workspace_tabs": _workspace_tabs(
            partner_id=partner.id,
        ),
    }


def _setup_steps(*, setup_status, bot_instance, primary_contact, partner):
    missing = set(setup_status.missing_steps)
    checked = bool(
        bot_instance
        and bot_instance.last_checked_at
        and bot_instance.telegram_bot_id
        and bot_instance.telegram_username
        and not bot_instance.last_error_code
    )
    launched = bool(
        bot_instance
        and bot_instance.status == BotInstance.Status.ACTIVE
        and partner.status == partner.Status.ACTIVE
    )
    return (
        ("Bot token", SetupStep.BOT_TOKEN not in missing),
        ("Language", SetupStep.LANGUAGE not in missing),
        ("Timezone", SetupStep.TIMEZONE not in missing),
        ("Category and service", SetupStep.CATALOG_SERVICE not in missing),
        ("Availability window", SetupStep.AVAILABILITY_WINDOW not in missing),
        (
            "Booking confirmation mode",
            SetupStep.BOOKING_CONFIRMATION_MODE not in missing,
        ),
        ("Telegram connection checked", checked),
        ("Master Telegram contact", primary_contact is not None),
        ("Bot launched", launched),
    )


def _content_groups(*, overrides) -> tuple[dict, ...]:
    override_keys = {override.key for override in overrides if override.is_active and override.body}
    groups: dict[str, dict[str, object]] = {}
    for definition in SYSTEM_TEMPLATE_DEFINITIONS:
        if not definition.editable:
            continue
        prefix = definition.key.split(".", 1)[0]
        group = groups.setdefault(
            prefix,
            {
                "slug": prefix,
                "label": CONTENT_GROUP_LABELS.get(prefix, prefix.replace("_", " ").title()),
                "key_count": 0,
                "overridden_key_count": 0,
            },
        )
        group["key_count"] += 1
        if definition.key in override_keys:
            group["overridden_key_count"] += 1
    return tuple(groups.values())


def _workspace_tabs(*, partner_id) -> tuple[BotWorkspaceTab, ...]:
    return tuple(
        BotWorkspaceTab(
            slug=slug,
            label=label,
            url=_workspace_url(partner_id=partner_id, tab=slug),
        )
        for slug, label in BOT_WORKSPACE_TAB_LABELS.items()
    )


def _instance_action_success(action: str) -> str:
    return {
        BOT_WORKSPACE_ACTION_CHECK_CONNECTION: "Telegram connection checked.",
        BOT_WORKSPACE_ACTION_LAUNCH: "Bot launched. Restart the polling runner if needed.",
        BOT_WORKSPACE_ACTION_PAUSE: "Bot paused.",
    }[action]


def _workspace_url(*, partner_id, tab: str) -> str:
    return f"{reverse('bot:workspace')}?{urlencode({'partner': partner_id, 'tab': tab})}"


def _selected_partner(*, request, partners):
    selected_partner_id = request.GET.get("partner", "").strip()
    if not selected_partner_id:
        return partners[0] if partners else None
    try:
        selected_uuid = UUID(selected_partner_id)
    except (TypeError, ValueError) as exc:
        raise Http404("Partner not found.") from exc
    for partner in partners:
        if partner.id == selected_uuid:
            return partner
    raise Http404("Partner not found.")


def _selected_tab(request) -> str:
    selected_tab = request.GET.get("tab", BOT_WORKSPACE_DEFAULT_TAB).strip()
    if selected_tab not in BOT_WORKSPACE_TAB_LABELS:
        raise Http404("Bot workspace tab not found.")
    return selected_tab


def _error_text(exc: PermissionDenied | ValidationError) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(exc.messages)
    return str(exc)
