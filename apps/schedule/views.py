from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import urlencode
from uuid import UUID

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone

from apps.partners.selectors import visible_partners_for_user
from apps.schedule.forms import (
    CalendarCopyScheduleForm,
    CalendarQuickCreateForm,
    add_domain_errors_to_calendar_copy_form,
    add_domain_errors_to_calendar_quick_create_form,
)
from apps.schedule.selectors import (
    active_booking_resources_for_partner,
    partner_calendar_snapshot,
)
from apps.schedule.services import (
    copy_schedule_day,
    copy_schedule_week,
    create_availability_window,
    create_blocked_time,
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

    form_action = request.POST.get("form_action", "") if request.method == "POST" else ""
    quick_create_submission = form_action == "quick_create"
    quick_create_open = quick_create_submission or request.GET.get("create") == "1"
    quick_create_form = None
    if selected_partner is not None and selected_resource is not None and quick_create_open:
        quick_create_form = CalendarQuickCreateForm(
            request.POST if quick_create_submission else None,
            partner=selected_partner,
            initial={
                "item_type": CalendarQuickCreateForm.AVAILABILITY,
                "day": _selected_quick_create_day(request=request, fallback=selected_week),
            },
        )
        if quick_create_submission and quick_create_form.is_valid():
            try:
                response = _run_quick_create(
                    request=request,
                    form=quick_create_form,
                    partner=selected_partner,
                    booking_resource=selected_resource,
                    week_start=selected_week,
                )
            except ValidationError as exc:
                add_domain_errors_to_calendar_quick_create_form(quick_create_form, exc)
            else:
                return response

    copy_scope = _selected_copy_scope(request=request)
    if form_action == "copy_schedule" and copy_scope is None:
        raise Http404("Calendar copy scope not found.")
    copy_source_date = (
        _selected_copy_source_date(
            request=request,
            scope=copy_scope,
            fallback=selected_week,
        )
        if copy_scope is not None
        else None
    )
    copy_schedule_submission = form_action == "copy_schedule"
    copy_schedule_form = None
    if (
        selected_partner is not None
        and selected_resource is not None
        and copy_scope is not None
        and copy_source_date is not None
    ):
        copy_schedule_form = CalendarCopyScheduleForm(
            request.POST if copy_schedule_submission else None,
            scope=copy_scope,
            source_date=copy_source_date,
            initial={
                "target_date": _default_copy_target_date(
                    partner=selected_partner,
                    scope=copy_scope,
                    source_date=copy_source_date,
                )
            },
        )
        if copy_schedule_submission and copy_schedule_form.is_valid():
            try:
                response = _run_schedule_copy(
                    request=request,
                    form=copy_schedule_form,
                    scope=copy_scope,
                    source_date=copy_source_date,
                    partner=selected_partner,
                    booking_resource=selected_resource,
                )
            except ValidationError as exc:
                add_domain_errors_to_calendar_copy_form(copy_schedule_form, exc)
            else:
                return response

    context = {
        **admin.site.each_context(request),
        "is_nav_sidebar_enabled": False,
        "title": "Calendar",
        "partners": partners,
        "selected_partner": selected_partner,
        "resources": resources,
        "selected_resource": selected_resource,
        "snapshot": snapshot,
        "quick_create_open": quick_create_open,
        "quick_create_form": quick_create_form,
        "copy_scope": copy_scope,
        "copy_source_date": copy_source_date,
        "copy_schedule_form": copy_schedule_form,
    }
    return TemplateResponse(request, "schedule/calendar.html", context)


def _run_quick_create(*, request, form, partner, booking_resource, week_start):
    item_type = form.cleaned_data["item_type"]
    if item_type == CalendarQuickCreateForm.APPOINTMENT:
        query = urlencode(
            {
                "partner": partner.id,
                "booking_resource": booking_resource.id,
                "start_at": form.cleaned_data["start_at"].strftime("%Y-%m-%dT%H:%M"),
            }
        )
        return HttpResponseRedirect(f"{reverse('admin:appointments_appointment_add')}?{query}")

    if item_type == CalendarQuickCreateForm.AVAILABILITY:
        create_availability_window(
            actor=request.user,
            partner=partner,
            booking_resource=booking_resource,
            start_at=form.cleaned_data["start_at"],
            end_at=form.cleaned_data["end_at"],
        )
        success_message = "Availability window created."
    else:
        create_blocked_time(
            actor=request.user,
            partner=partner,
            booking_resource=booking_resource,
            start_at=form.cleaned_data["start_at"],
            end_at=form.cleaned_data["end_at"],
            reason=form.cleaned_data["reason"],
        )
        success_message = "Blocked time created."

    messages.success(request, success_message)
    return HttpResponseRedirect(
        _calendar_url(
            partner_id=partner.id,
            resource_id=booking_resource.id,
            week_start=week_start,
        )
    )


def _run_schedule_copy(
    *,
    request,
    form,
    scope,
    source_date,
    partner,
    booking_resource,
):
    target_date = form.cleaned_data["target_date"]
    if scope == CalendarCopyScheduleForm.DAY:
        result = copy_schedule_day(
            actor=request.user,
            partner=partner,
            booking_resource=booking_resource,
            source_day=source_date,
            target_day=target_date,
        )
    else:
        result = copy_schedule_week(
            actor=request.user,
            partner=partner,
            booking_resource=booking_resource,
            source_week_start=source_date,
            target_week_start=target_date,
        )

    if result.created_total:
        messages.success(
            request,
            (
                f"Copied {result.availability_created} availability window(s) and "
                f"{result.blocked_created} blocked interval(s). "
                f"Skipped {result.skipped_existing} existing item(s)."
            ),
        )
    elif result.skipped_existing:
        messages.info(request, "All matching schedule items already exist in the target period.")
    else:
        messages.info(request, "No availability or blocked time starts in the source period.")

    target_week = target_date - timedelta(days=target_date.weekday())
    return HttpResponseRedirect(
        _calendar_url(
            partner_id=partner.id,
            resource_id=booking_resource.id,
            week_start=target_week,
        )
    )


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


def _selected_quick_create_day(*, request, fallback: date) -> date:
    raw_day = request.GET.get("date", "").strip()
    if not raw_day:
        return fallback
    try:
        return date.fromisoformat(raw_day)
    except ValueError as exc:
        raise Http404("Calendar date not found.") from exc


def _selected_copy_scope(*, request) -> str | None:
    raw_scope = request.GET.get("copy", "").strip()
    if not raw_scope:
        return None
    if raw_scope not in CalendarCopyScheduleForm.SCOPES:
        raise Http404("Calendar copy scope not found.")
    return raw_scope


def _selected_copy_source_date(*, request, scope: str, fallback: date) -> date:
    raw_source_date = request.GET.get("source", "").strip()
    if raw_source_date:
        try:
            source_date = date.fromisoformat(raw_source_date)
        except ValueError as exc:
            raise Http404("Calendar copy source not found.") from exc
    else:
        source_date = fallback
    if source_date < date.min + timedelta(days=31) or source_date > date.max - timedelta(days=31):
        raise Http404("Calendar copy source not found.")
    if scope == CalendarCopyScheduleForm.WEEK:
        return source_date - timedelta(days=source_date.weekday())
    return source_date


def _default_copy_target_date(*, partner, scope: str, source_date: date) -> date:
    local_today = timezone.localdate(timezone=get_zone(partner.timezone))
    if scope == CalendarCopyScheduleForm.DAY:
        return max(source_date + timedelta(days=1), local_today + timedelta(days=1))
    current_week_start = local_today - timedelta(days=local_today.weekday())
    return max(source_date + timedelta(days=7), current_week_start + timedelta(days=7))


def _calendar_url(*, partner_id, resource_id, week_start: date) -> str:
    query = urlencode(
        {
            "partner": partner_id,
            "resource": resource_id,
            "week": week_start.isoformat(),
        }
    )
    return f"{reverse('schedule:calendar')}?{query}"


def _parse_uuid(raw_value: str, *, message: str) -> UUID:
    try:
        return UUID(raw_value)
    except (TypeError, ValueError) as exc:
        raise Http404(message) from exc
