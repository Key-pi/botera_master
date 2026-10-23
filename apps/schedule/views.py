from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import urlencode
from uuid import UUID

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone

from apps.appointments.models import Appointment
from apps.appointments.selectors import appointment_for_booking_resource_by_id
from apps.appointments.services import available_appointment_status_targets
from apps.partners.selectors import visible_partners_for_user
from apps.schedule.forms import (
    CalendarCopyScheduleForm,
    CalendarQuickCreateForm,
    ScheduleConflictActionForm,
    add_domain_errors_to_calendar_copy_form,
    add_domain_errors_to_calendar_quick_create_form,
)
from apps.schedule.selectors import (
    CALENDAR_VIEW_WEEK,
    CALENDAR_VIEWS,
    active_booking_resources_for_partner,
    booking_resources_for_partner,
    partner_calendar_snapshot,
    upcoming_schedule_conflicts_for_partner,
)
from apps.schedule.services import (
    acknowledge_schedule_conflicts,
    copy_schedule_day,
    copy_schedule_week,
    create_availability_window,
    create_blocked_time,
    reopen_schedule_conflicts,
)
from core.timezones import get_zone

CONFLICT_MODE_ACTIVE = "active"
CONFLICT_MODE_KEPT_AS_IS = "kept_as_is"
CONFLICT_MODES = (CONFLICT_MODE_ACTIVE, CONFLICT_MODE_KEPT_AS_IS)


@staff_member_required
def calendar(request):
    """Render a tenant-safe operational calendar for one booking resource."""

    partners = tuple(visible_partners_for_user(request.user).order_by("name", "id"))
    selected_partner = _selected_partner(request=request, partners=partners)
    resources = (
        tuple(active_booking_resources_for_partner(selected_partner)) if selected_partner else ()
    )
    selected_resource = _selected_resource(request=request, resources=resources)
    selected_view = _selected_calendar_view(request=request)
    selected_date = _selected_calendar_date(request=request, partner=selected_partner)
    snapshot = None
    if selected_partner is not None and selected_resource is not None:
        snapshot = partner_calendar_snapshot(
            partner=selected_partner,
            booking_resource=selected_resource,
            selected_date=selected_date,
            view=selected_view,
        )
    selected_appointment = _selected_calendar_appointment(
        request=request,
        partner=selected_partner,
        booking_resource=selected_resource,
    )
    appointment_actions = (
        _calendar_appointment_actions(
            request=request,
            appointment=selected_appointment,
        )
        if selected_appointment is not None
        else None
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
                "day": _selected_quick_create_day(request=request, fallback=selected_date),
            },
        )
        if quick_create_submission and quick_create_form.is_valid():
            try:
                response = _run_quick_create(
                    request=request,
                    form=quick_create_form,
                    partner=selected_partner,
                    booking_resource=selected_resource,
                    calendar_view=selected_view,
                    selected_date=selected_date,
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
            fallback=selected_date,
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
                    calendar_view=selected_view,
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
        "selected_view": selected_view,
        "selected_date": selected_date,
        "snapshot": snapshot,
        "quick_create_open": quick_create_open,
        "quick_create_form": quick_create_form,
        "copy_scope": copy_scope,
        "copy_source_date": copy_source_date,
        "copy_schedule_form": copy_schedule_form,
        "selected_appointment": selected_appointment,
        "appointment_actions": appointment_actions,
        "calendar_url": (
            _calendar_url(
                partner_id=selected_partner.id,
                resource_id=selected_resource.id,
                calendar_view=selected_view,
                selected_date=selected_date,
            )
            if selected_partner is not None and selected_resource is not None
            else None
        ),
    }
    return TemplateResponse(request, "schedule/calendar.html", context)


@staff_member_required
def conflict_list(request):
    """Render computed future schedule conflicts inside trusted partner scope."""

    partners = tuple(visible_partners_for_user(request.user).order_by("name", "id"))
    selected_partner = _selected_partner(request=request, partners=partners)
    resources = (
        tuple(booking_resources_for_partner(selected_partner)) if selected_partner else ()
    )
    selected_resource = _selected_optional_resource(request=request, resources=resources)
    all_conflicts = (
        upcoming_schedule_conflicts_for_partner(
            partner=selected_partner,
            booking_resource=selected_resource,
            include_acknowledged=True,
        )
        if selected_partner is not None
        else ()
    )
    active_conflicts = tuple(
        conflict for conflict in all_conflicts if not conflict.is_acknowledged
    )
    kept_conflicts = tuple(
        conflict for conflict in all_conflicts if conflict.is_acknowledged
    )
    conflict_mode = _selected_conflict_mode(request=request)
    conflicts = (
        kept_conflicts if conflict_mode == CONFLICT_MODE_KEPT_AS_IS else active_conflicts
    )
    action_form = ScheduleConflictActionForm(
        request.POST if request.method == "POST" else None,
        conflicts=all_conflicts,
    )
    if request.method == "POST" and selected_partner is not None and action_form.is_valid():
        try:
            if action_form.cleaned_data["action"] == ScheduleConflictActionForm.KEEP_AS_IS:
                changed_count = len(
                    acknowledge_schedule_conflicts(
                        actor=request.user,
                        partner=selected_partner,
                        appointment_ids=action_form.appointment_ids,
                    )
                )
                success_message = f"Kept {changed_count} schedule conflict(s) as is."
                redirect_mode = CONFLICT_MODE_ACTIVE
            else:
                changed_count = reopen_schedule_conflicts(
                    actor=request.user,
                    partner=selected_partner,
                    appointment_ids=action_form.appointment_ids,
                )
                success_message = f"Reopened {changed_count} schedule conflict(s)."
                redirect_mode = CONFLICT_MODE_KEPT_AS_IS
        except ValidationError as exc:
            for error in exc.messages:
                action_form.add_error(None, error)
        else:
            messages.success(request, success_message)
            return HttpResponseRedirect(
                _conflict_list_url(
                    partner_id=selected_partner.id,
                    resource_id=(selected_resource.id if selected_resource else None),
                    mode=redirect_mode,
                )
            )

    paginator = Paginator(conflicts, 20)
    page_obj = paginator.get_page(request.GET.get("page"))
    conflict_rows = tuple(
        {
            "conflict": conflict,
            "actions": _calendar_appointment_actions(
                request=request,
                appointment=conflict.appointment,
            ),
            "calendar_url": _calendar_appointment_url(conflict.appointment),
        }
        for conflict in page_obj.object_list
    )
    filter_query = {"partner": selected_partner.id} if selected_partner is not None else {}
    if selected_resource is not None:
        filter_query["resource"] = selected_resource.id
    if conflict_mode != CONFLICT_MODE_ACTIVE:
        filter_query["show"] = conflict_mode

    context = {
        **admin.site.each_context(request),
        "is_nav_sidebar_enabled": False,
        "title": "Schedule conflicts",
        "partners": partners,
        "selected_partner": selected_partner,
        "resources": resources,
        "selected_resource": selected_resource,
        "conflict_mode": conflict_mode,
        "active_conflict_count": len(active_conflicts),
        "kept_conflict_count": len(kept_conflicts),
        "conflict_count": len(conflicts),
        "conflict_rows": conflict_rows,
        "action_form": action_form,
        "conflict_action_url": _conflict_list_url(
            partner_id=selected_partner.id,
            resource_id=(selected_resource.id if selected_resource else None),
            mode=conflict_mode,
        )
        if selected_partner is not None
        else "",
        "page_obj": page_obj,
        "pagination_query": urlencode(filter_query),
    }
    return TemplateResponse(request, "schedule/conflicts.html", context)


def _run_quick_create(
    *,
    request,
    form,
    partner,
    booking_resource,
    calendar_view,
    selected_date,
):
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
            calendar_view=calendar_view,
            selected_date=selected_date,
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
    calendar_view,
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

    return HttpResponseRedirect(
        _calendar_url(
            partner_id=partner.id,
            resource_id=booking_resource.id,
            calendar_view=calendar_view,
            selected_date=target_date,
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


def _selected_optional_resource(*, request, resources):
    raw_resource_id = request.GET.get("resource", "").strip()
    if not raw_resource_id:
        return None

    selected_id = _parse_uuid(raw_resource_id, message="Calendar resource not found.")
    for resource in resources:
        if resource.id == selected_id:
            return resource
    raise Http404("Calendar resource not found.")


def _selected_conflict_mode(*, request) -> str:
    mode = request.GET.get("show", CONFLICT_MODE_ACTIVE).strip()
    if mode not in CONFLICT_MODES:
        raise Http404("Schedule conflict view not found.")
    return mode


def _selected_calendar_appointment(*, request, partner, booking_resource):
    raw_appointment_id = request.GET.get("appointment", "").strip()
    if not raw_appointment_id:
        return None
    if partner is None or booking_resource is None:
        raise Http404("Appointment not found.")

    appointment_id = _parse_uuid(raw_appointment_id, message="Appointment not found.")
    appointment = appointment_for_booking_resource_by_id(
        partner=partner,
        booking_resource=booking_resource,
        appointment_id=appointment_id,
    )
    if appointment is None:
        raise Http404("Appointment not found.")
    return appointment


def _calendar_appointment_actions(*, request, appointment):
    can_view_appointment = request.user.has_perm("appointments.view_appointment")
    can_change_appointment = request.user.has_perm("appointments.change_appointment")
    can_view_client = request.user.has_perm("clients.view_clientprofile")
    can_message_client = request.user.has_perm("messaging.add_conversationmessage")
    status_targets = (
        set(available_appointment_status_targets(appointment=appointment))
        if can_change_appointment
        else set()
    )
    status_url = (
        reverse("admin:appointments_appointment_status", args=[appointment.id])
        if status_targets
        else None
    )

    def status_target_url(target: str) -> str | None:
        if status_url is None or target not in status_targets:
            return None
        return f"{status_url}?{urlencode({'target': target})}"

    has_telegram = appointment.client_profile.telegram_identity_id is not None
    can_propose_time = (
        can_change_appointment
        and has_telegram
        and appointment.status
        in {
            Appointment.Status.REQUESTED,
            Appointment.Status.CONFIRMED,
        }
    )
    return {
        "record_url": (
            reverse("admin:appointments_appointment_change", args=[appointment.id])
            if can_view_appointment
            else None
        ),
        "client_url": (
            reverse(
                "admin:clients_clientprofile_change",
                args=[appointment.client_profile_id],
            )
            if can_view_client
            else None
        ),
        "status_url": status_url,
        "confirm_url": status_target_url(Appointment.Status.CONFIRMED),
        "cancel_url": status_target_url(Appointment.Status.CANCELLED_BY_MASTER),
        "complete_url": status_target_url(Appointment.Status.COMPLETED),
        "no_show_url": status_target_url(Appointment.Status.NO_SHOW),
        "proposal_url": (
            reverse(
                "admin:appointments_appointment_propose_time",
                args=[appointment.id],
            )
            if can_propose_time
            else None
        ),
        "message_url": (
            reverse(
                "admin:messaging_conversationmessage_reply",
                args=[appointment.client_profile_id],
            )
            if can_message_client and has_telegram
            else None
        ),
    }


def _selected_calendar_view(*, request) -> str:
    selected_view = request.GET.get("view", CALENDAR_VIEW_WEEK).strip()
    if selected_view not in CALENDAR_VIEWS:
        raise Http404("Calendar view not found.")
    return selected_view


def _selected_calendar_date(*, request, partner) -> date:
    if partner is None:
        return timezone.localdate()

    # Keep legacy week links valid while all newly rendered links use date.
    raw_date = request.GET.get("date", "").strip() or request.GET.get("week", "").strip()
    if raw_date:
        try:
            selected_day = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise Http404("Calendar date not found.") from exc
    else:
        selected_day = timezone.localdate(timezone=get_zone(partner.timezone))

    if (
        selected_day < date.min + timedelta(days=31)
        or selected_day > date.max - timedelta(days=31)
    ):
        raise Http404("Calendar date not found.")
    return selected_day


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


def _calendar_url(*, partner_id, resource_id, calendar_view: str, selected_date: date) -> str:
    query = urlencode(
        {
            "partner": partner_id,
            "resource": resource_id,
            "view": calendar_view,
            "date": selected_date.isoformat(),
        }
    )
    return f"{reverse('schedule:calendar')}?{query}"


def _calendar_appointment_url(appointment: Appointment) -> str | None:
    if not appointment.booking_resource.is_active:
        return None
    selected_date = appointment.start_at.astimezone(
        get_zone(appointment.partner.timezone)
    ).date()
    return (
        _calendar_url(
            partner_id=appointment.partner_id,
            resource_id=appointment.booking_resource_id,
            calendar_view=CALENDAR_VIEW_WEEK,
            selected_date=selected_date,
        )
        + f"&appointment={appointment.id}#appointment-detail"
    )


def _conflict_list_url(*, partner_id, resource_id=None, mode: str) -> str:
    query = {"partner": partner_id}
    if resource_id is not None:
        query["resource"] = resource_id
    if mode != CONFLICT_MODE_ACTIVE:
        query["show"] = mode
    return f"{reverse('schedule:conflicts')}?{urlencode(query)}"


def _parse_uuid(raw_value: str, *, message: str) -> UUID:
    try:
        return UUID(raw_value)
    except (TypeError, ValueError) as exc:
        raise Http404(message) from exc
