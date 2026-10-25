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

from apps.bot.content import (
    SYSTEM_TEMPLATE_DEFINITION_BY_KEY,
    SYSTEM_TEMPLATE_DEFINITIONS,
    TemplateDefinition,
)
from apps.bot.forms import (
    BOT_WORKSPACE_ACTION_CHECK_CONNECTION,
    BOT_WORKSPACE_ACTION_CREATE_MASTER_LINK,
    BOT_WORKSPACE_ACTION_LAUNCH,
    BOT_WORKSPACE_ACTION_PAUSE,
    BOT_WORKSPACE_ACTION_PREVIEW_CONTENT,
    BOT_WORKSPACE_ACTION_RESET_CONTENT,
    BOT_WORKSPACE_ACTION_SAVE_CONTENT,
    BotContentWorkspaceActionForm,
    BotContentWorkspaceForm,
    BotTokenWorkspaceForm,
    BotWorkspaceActionForm,
)
from apps.bot.models import DEFAULT_TEMPLATE_LANGUAGE, SUPPORTED_TEMPLATE_LANGUAGES, BotInstance
from apps.bot.selectors import (
    bot_instance_for_partner,
    primary_master_telegram_contact,
    template_override_for_partner,
    template_overrides_for_partner,
)
from apps.bot.services import (
    activate_partner_bot,
    check_bot_connection,
    create_master_contact_link,
    preview_template_body,
    render_content_template,
    reset_partner_template_override,
    set_bot_status,
    set_bot_token,
    set_partner_template_override,
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
    content_preview = None
    selected_content_definition = None
    selected_content_language = ""
    content_form = None
    token_form = (
        BotTokenWorkspaceForm(partner=selected_partner) if selected_partner is not None else None
    )
    if selected_partner is not None and selected_tab == "content":
        selected_content_definition = _selected_content_definition(request)
        selected_content_language = _selected_content_language(
            request=request,
            partner=selected_partner,
        )
        selected_override = template_override_for_partner(
            partner=selected_partner,
            key=selected_content_definition.key,
            language=selected_content_language,
        )
        content_form = BotContentWorkspaceForm(
            definition=selected_content_definition,
            initial={"body": selected_override.body if selected_override else ""},
        )

    if request.method == "POST":
        if selected_partner is None:
            raise Http404("Partner not found.")
        action = request.POST.get("action", "").strip()
        if selected_tab == "instance":
            if action == "replace_token":
                token_form = BotTokenWorkspaceForm(request.POST, partner=selected_partner)
                if token_form.is_valid():
                    set_bot_token(
                        actor=request.user,
                        partner=selected_partner,
                        raw_token=token_form.cleaned_data["new_token"],
                    )
                    messages.success(
                        request,
                        "Bot token saved securely. Check the connection next.",
                    )
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
        elif selected_tab == "content":
            action_form = BotContentWorkspaceActionForm(request.POST)
            if not action_form.is_valid():
                messages.error(request, "Unknown content action.")
            elif action == BOT_WORKSPACE_ACTION_RESET_CONTENT:
                try:
                    reset_partner_template_override(
                        actor=request.user,
                        partner=selected_partner,
                        key=selected_content_definition.key,
                        language=selected_content_language,
                    )
                except (PermissionDenied, ValidationError) as exc:
                    messages.warning(request, _error_text(exc))
                else:
                    messages.success(request, "Content reset to the system default.")
                    return HttpResponseRedirect(
                        _workspace_url(
                            partner_id=selected_partner.id,
                            tab="content",
                            content_key=selected_content_definition.key,
                            language=selected_content_language,
                        )
                    )
            else:
                content_form = BotContentWorkspaceForm(
                    request.POST,
                    definition=selected_content_definition,
                )
                if content_form.is_valid():
                    try:
                        if action == BOT_WORKSPACE_ACTION_PREVIEW_CONTENT:
                            content_preview = preview_template_body(
                                key=selected_content_definition.key,
                                language=selected_content_language,
                                body=content_form.cleaned_data["body"],
                            )
                        elif action == BOT_WORKSPACE_ACTION_SAVE_CONTENT:
                            if content_form.cleaned_data["body"]:
                                set_partner_template_override(
                                    actor=request.user,
                                    partner=selected_partner,
                                    key=selected_content_definition.key,
                                    language=selected_content_language,
                                    body=content_form.cleaned_data["body"],
                                )
                            else:
                                reset_partner_template_override(
                                    actor=request.user,
                                    partner=selected_partner,
                                    key=selected_content_definition.key,
                                    language=selected_content_language,
                                )
                    except (PermissionDenied, ValidationError) as exc:
                        content_form.add_error(None, _error_text(exc))
                    else:
                        if action == BOT_WORKSPACE_ACTION_SAVE_CONTENT:
                            messages.success(request, "Localized bot content saved.")
                            return HttpResponseRedirect(
                                _workspace_url(
                                    partner_id=selected_partner.id,
                                    tab="content",
                                    content_key=selected_content_definition.key,
                                    language=selected_content_language,
                                )
                            )
        else:
            messages.error(request, "This bot workspace tab is read-only for now.")

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
                content_definition=selected_content_definition,
                content_language=selected_content_language,
                content_form=content_form,
                content_preview=content_preview,
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


def _workspace_context(
    *,
    partner,
    content_definition: TemplateDefinition | None = None,
    content_language: str = "",
    content_form: BotContentWorkspaceForm | None = None,
    content_preview=None,
) -> dict:
    bot_instance = bot_instance_for_partner(partner)
    setup_status = get_partner_setup_status(partner)
    primary_contact = primary_master_telegram_contact(partner)
    notification_settings = notification_settings_for_partner(partner=partner)
    reminder_rules = tuple(reminder_rules_for_partner(partner=partner))
    schedule_settings = schedule_settings_for_partner(partner)
    penalty_settings = penalty_settings_for_partner(partner=partner)
    overrides = tuple(template_overrides_for_partner(partner))

    context = {
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
        "content_groups": _content_groups(
            overrides=overrides,
            partner_id=partner.id,
            selected_key=content_definition.key if content_definition else "",
            language=content_language or partner.default_language,
        ),
        "content_override_count": len(overrides),
        "workspace_tabs": _workspace_tabs(
            partner_id=partner.id,
        ),
    }
    if content_definition is not None:
        context.update(
            _content_editor_context(
                partner=partner,
                definition=content_definition,
                language=content_language,
                overrides=overrides,
                content_form=content_form,
                preview=content_preview,
            )
        )
    return context


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


def _content_groups(
    *,
    overrides,
    partner_id,
    selected_key: str,
    language: str,
) -> tuple[dict, ...]:
    override_pairs = {
        (override.key, override.language)
        for override in overrides
        if override.is_active and override.body
    }
    override_keys = {key for key, _language in override_pairs}
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
                "items": [],
                "is_selected": False,
            },
        )
        group["key_count"] += 1
        if definition.key in override_keys:
            group["overridden_key_count"] += 1
        item = {
            "key": definition.key,
            "label": _template_label(definition.key),
            "overridden_language_count": sum(
                (definition.key, template_language) in override_pairs
                for template_language in SUPPORTED_TEMPLATE_LANGUAGES
            ),
            "is_selected": definition.key == selected_key,
            "url": _workspace_url(
                partner_id=partner_id,
                tab="content",
                content_key=definition.key,
                language=language,
            ),
        }
        group["items"].append(item)
        if item["is_selected"]:
            group["is_selected"] = True
    return tuple(groups.values())


def _content_editor_context(
    *,
    partner,
    definition: TemplateDefinition,
    language: str,
    overrides,
    content_form: BotContentWorkspaceForm | None,
    preview,
) -> dict:
    selected_override = next(
        (
            override
            for override in overrides
            if override.key == definition.key and override.language == language
        ),
        None,
    )
    if content_form is None:
        content_form = BotContentWorkspaceForm(
            definition=definition,
            initial={"body": selected_override.body if selected_override else ""},
        )
    effective_preview = preview or render_content_template(
        partner=partner,
        key=definition.key,
        language=language,
        context=definition.sample_context,
    )
    active_override_pairs = {
        (override.key, override.language)
        for override in overrides
        if override.is_active and override.body
    }
    return {
        "content_form": content_form,
        "selected_content_definition": definition,
        "selected_content_key": definition.key,
        "selected_content_label": _template_label(definition.key),
        "selected_content_language": language,
        "selected_content_override": selected_override,
        "content_languages": tuple(
            {
                "code": template_language,
                "label": template_language.upper(),
                "is_selected": template_language == language,
                "has_override": (definition.key, template_language) in active_override_pairs,
                "url": _workspace_url(
                    partner_id=partner.id,
                    tab="content",
                    content_key=definition.key,
                    language=template_language,
                ),
            }
            for template_language in SUPPORTED_TEMPLATE_LANGUAGES
        ),
        "content_system_default": definition.translations.get(
            language,
            definition.translations[DEFAULT_TEMPLATE_LANGUAGE],
        ),
        "content_allowed_placeholders": definition.placeholders,
        "content_preview": effective_preview,
        "content_preview_is_draft": preview is not None,
        "content_preview_source": {
            "draft": "Unsaved draft",
            "partner_override": "Partner override",
            "system_default": "System default",
        }.get(effective_preview.source, effective_preview.source),
        "content_type_label": definition.content_type.replace("_", " ").title(),
    }


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


def _workspace_url(
    *,
    partner_id,
    tab: str,
    content_key: str = "",
    language: str = "",
) -> str:
    params = {"partner": partner_id, "tab": tab}
    if content_key:
        params["key"] = content_key
    if language:
        params["language"] = language
    return f"{reverse('bot:workspace')}?{urlencode(params)}"


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


def _selected_content_definition(request) -> TemplateDefinition:
    editable_definitions = tuple(
        definition for definition in SYSTEM_TEMPLATE_DEFINITIONS if definition.editable
    )
    selected_key = request.GET.get("key", "").strip()
    if not selected_key:
        return editable_definitions[0]
    definition = SYSTEM_TEMPLATE_DEFINITION_BY_KEY.get(selected_key)
    if definition is None or not definition.editable:
        raise Http404("Editable content template not found.")
    return definition


def _selected_content_language(*, request, partner) -> str:
    selected_language = request.GET.get("language", "").strip().lower()
    if not selected_language:
        selected_language = partner.default_language or DEFAULT_TEMPLATE_LANGUAGE
    if selected_language not in SUPPORTED_TEMPLATE_LANGUAGES:
        raise Http404("Content language not found.")
    return selected_language


def _template_label(key: str) -> str:
    suffix = key.split(".", 1)[-1]
    return suffix.replace("_", " ").strip().title()


def _error_text(exc: PermissionDenied | ValidationError) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(exc.messages)
    return str(exc)
