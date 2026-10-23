from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404
from django.template.response import TemplateResponse
from django.utils import timezone

from apps.partners.selectors import visible_partners_for_user
from apps.schedule.selectors import (
    active_booking_resources_for_partner,
    partner_calendar_snapshot,
)
from core.timezones import get_zone


@staff_member_required
def calendar(request):
    """Render a tenant-safe operational week for one booking resource."""

    partners = tuple(visible_partners_for_user(request.user).order_by("name", "id"))
    selected_partner = _selected_partner(request=request, partners=partners)
    resources = (
        tuple(active_booking_resources_for_partner(selected_partner)) if selected_partner else ()
    )
    selected_resource = _selected_resource(request=request, resources=resources)
    selected_week = _selected_week(request=request, partner=selected_partner)
    snapshot = None
    if selected_partner is not None and selected_resource is not None:
        snapshot = partner_calendar_snapshot(
            partner=selected_partner,
            booking_resource=selected_resource,
            week_start=selected_week,
        )

    context = {
        **admin.site.each_context(request),
        "is_nav_sidebar_enabled": False,
        "title": "Calendar",
        "partners": partners,
        "selected_partner": selected_partner,
        "resources": resources,
        "selected_resource": selected_resource,
        "snapshot": snapshot,
    }
    return TemplateResponse(request, "schedule/calendar.html", context)


def _selected_partner(*, request, partners):
    raw_partner_id = request.GET.get("partner", "").strip()
    if not raw_partner_id:
        return partners[0] if partners else None

    selected_id = _parse_uuid(raw_partner_id, message="Partner not found.")
    for partner in partners:
        if partner.id == selected_id:
            return partner
    raise Http404("Partner not found.")


def _selected_resource(*, request, resources):
    raw_resource_id = request.GET.get("resource", "").strip()
    if not raw_resource_id:
        return next(
            (resource for resource in resources if resource.is_default),
            resources[0] if resources else None,
        )

    selected_id = _parse_uuid(raw_resource_id, message="Calendar resource not found.")
    for resource in resources:
        if resource.id == selected_id:
            return resource
    raise Http404("Calendar resource not found.")


def _selected_week(*, request, partner) -> date:
    if partner is None:
        return timezone.localdate()

    raw_week = request.GET.get("week", "").strip()
    if raw_week:
        try:
            selected_day = date.fromisoformat(raw_week)
        except ValueError as exc:
            raise Http404("Calendar week not found.") from exc
    else:
        selected_day = timezone.localdate(timezone=get_zone(partner.timezone))

    if selected_day < date.min + timedelta(days=7) or selected_day > date.max - timedelta(days=14):
        raise Http404("Calendar week not found.")
    return selected_day - timedelta(days=selected_day.weekday())


def _parse_uuid(raw_value: str, *, message: str) -> UUID:
    try:
        return UUID(raw_value)
    except (TypeError, ValueError) as exc:
        raise Http404(message) from exc
