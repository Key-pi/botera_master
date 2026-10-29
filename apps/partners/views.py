from dataclasses import dataclass
from urllib.parse import urlencode
from uuid import UUID

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404
from django.template.response import TemplateResponse
from django.urls import reverse

from apps.partners.selectors import (
    SETUP_BOT_LAUNCHED,
    SETUP_CONNECTION_CHECKED,
    SETUP_MASTER_CONTACT,
    PartnerOnboardingStep,
    partner_dashboard_snapshot,
    partner_onboarding_snapshot,
    visible_partners_for_user,
)
from apps.partners.setup import SetupStep


@dataclass(frozen=True, slots=True)
class SetupGuideStep:
    """Presentation row for one partner onboarding action."""

    key: str
    label: str
    is_complete: bool
    action_label: str
    action_url: str


@staff_member_required
def dashboard(request):
    """Render the operational landing screen from trusted tenant scope."""

    partners = tuple(visible_partners_for_user(request.user).order_by("name", "id"))
    selected_partner = _selected_partner(request=request, partners=partners)
    context = {
        **admin.site.each_context(request),
        "is_nav_sidebar_enabled": False,
        "title": "Dashboard",
        "partners": partners,
        "snapshot": (
            partner_dashboard_snapshot(partner=selected_partner) if selected_partner else None
        ),
    }

    return TemplateResponse(
        request,
        "partners/dashboard.html",
        context,
    )


@staff_member_required
def setup(request):
    """Render one actionable setup guide inside trusted partner scope."""

    partners = tuple(visible_partners_for_user(request.user).order_by("name", "id"))
    selected_partner = _selected_partner(request=request, partners=partners)
    onboarding = (
        partner_onboarding_snapshot(partner=selected_partner) if selected_partner else None
    )
    context = {
        **admin.site.each_context(request),
        "is_nav_sidebar_enabled": False,
        "title": "Partner setup",
        "partners": partners,
        "onboarding": onboarding,
        "required_steps": (
            _setup_guide_steps(
                steps=onboarding.required_steps,
                partner_id=selected_partner.id,
            )
            if onboarding
            else ()
        ),
        "operational_steps": (
            _setup_guide_steps(
                steps=onboarding.operational_steps,
                partner_id=selected_partner.id,
            )
            if onboarding
            else ()
        ),
    }
    return TemplateResponse(request, "partners/setup.html", context)


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


def _setup_guide_steps(
    *,
    steps: tuple[PartnerOnboardingStep, ...],
    partner_id,
) -> tuple[SetupGuideStep, ...]:
    return tuple(
        SetupGuideStep(
            key=step.key,
            label=step.label,
            is_complete=step.is_complete,
            action_label=_setup_action_label(step.key),
            action_url=_setup_action_url(step.key, partner_id=partner_id),
        )
        for step in steps
    )


def _setup_action_label(step_key: str) -> str:
    return {
        SetupStep.BOT_TOKEN: "Configure bot",
        SetupStep.LANGUAGE: "Open partner settings",
        SetupStep.TIMEZONE: "Open partner settings",
        SetupStep.CATALOG_SERVICE: "Open catalog",
        SetupStep.AVAILABILITY_WINDOW: "Open calendar",
        SetupStep.BOOKING_CONFIRMATION_MODE: "Open booking settings",
        SETUP_CONNECTION_CHECKED: "Check connection",
        SETUP_MASTER_CONTACT: "Connect master Telegram",
        SETUP_BOT_LAUNCHED: "Open launch controls",
    }[step_key]


def _setup_action_url(step_key: str, *, partner_id) -> str:
    if step_key in {
        SetupStep.BOT_TOKEN,
        SETUP_CONNECTION_CHECKED,
        SETUP_MASTER_CONTACT,
        SETUP_BOT_LAUNCHED,
    }:
        return _url_with_query(
            reverse("bot:workspace"),
            partner=partner_id,
            tab="instance",
        )
    if step_key in {SetupStep.LANGUAGE, SetupStep.TIMEZONE}:
        return _url_with_query(
            reverse("bot:workspace"),
            partner=partner_id,
            tab="settings",
        )
    if step_key == SetupStep.CATALOG_SERVICE:
        return _url_with_query(
            reverse("admin:catalog_service_changelist"),
            partner__id__exact=partner_id,
        )
    if step_key == SetupStep.AVAILABILITY_WINDOW:
        return _url_with_query(reverse("schedule:calendar"), partner=partner_id)
    if step_key == SetupStep.BOOKING_CONFIRMATION_MODE:
        return _url_with_query(
            reverse("bot:workspace"),
            partner=partner_id,
            tab="booking",
        )
    raise ValueError(f"Unknown setup step: {step_key}")


def _url_with_query(base_url: str, **params) -> str:
    return f"{base_url}?{urlencode(params)}"
