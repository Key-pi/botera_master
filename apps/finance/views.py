from __future__ import annotations

from datetime import date
from urllib.parse import urlencode
from uuid import UUID

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.catalog.models import DEFAULT_CURRENCY
from apps.finance.selectors import (
    FINANCE_PERIOD_MONTH,
    FINANCE_PERIODS,
    expenses_for_partner,
    finance_period_window,
    finance_report_for_partner,
    shift_finance_period_anchor,
)
from apps.partners.selectors import visible_partners_for_user
from core.timezones import get_zone

FINANCE_EXPENSE_PREVIEW_LIMIT = 20


@staff_member_required
def report(request):
    """Render tenant-safe day/week/month finance totals and period expenses."""

    partners = tuple(visible_partners_for_user(request.user).order_by("name", "id"))
    selected_partner = _selected_partner(request=request, partners=partners)
    selected_period = _selected_period(request=request)
    now = timezone.now()
    selected_date = _selected_date(
        request=request,
        partner=selected_partner,
        now=now,
    )

    window = None
    finance_report = None
    expenses = ()
    expense_count = 0
    previous_url = None
    next_url = None
    period_urls = {}
    if selected_partner is not None:
        window = finance_period_window(
            period=selected_period,
            anchor_date=selected_date,
        )
        finance_report = finance_report_for_partner(
            partner=selected_partner,
            start_date=window.start_date,
            end_date=window.end_date,
            currency=DEFAULT_CURRENCY,
            now=now,
        )
        expense_queryset = expenses_for_partner(
            partner=selected_partner,
            start_date=window.start_date,
            end_date=window.end_date,
        ).filter(currency=DEFAULT_CURRENCY)
        expense_count = expense_queryset.count()
        expenses = tuple(expense_queryset[:FINANCE_EXPENSE_PREVIEW_LIMIT])
        previous_url = _report_url(
            partner_id=selected_partner.id,
            period=selected_period,
            anchor_date=shift_finance_period_anchor(
                period=selected_period,
                anchor_date=selected_date,
                steps=-1,
            ),
        )
        next_url = _report_url(
            partner_id=selected_partner.id,
            period=selected_period,
            anchor_date=shift_finance_period_anchor(
                period=selected_period,
                anchor_date=selected_date,
                steps=1,
            ),
        )
        period_urls = {
            period: _report_url(
                partner_id=selected_partner.id,
                period=period,
                anchor_date=selected_date,
            )
            for period in FINANCE_PERIODS
        }

    context = {
        **admin.site.each_context(request),
        "is_nav_sidebar_enabled": False,
        "title": _("Finance"),
        "partners": partners,
        "selected_partner": selected_partner,
        "selected_period": selected_period,
        "selected_date": selected_date,
        "window": window,
        "report": finance_report,
        "expenses": expenses,
        "expense_count": expense_count,
        "expense_preview_limit": FINANCE_EXPENSE_PREVIEW_LIMIT,
        "previous_url": previous_url,
        "next_url": next_url,
        "period_urls": period_urls,
    }
    return TemplateResponse(request, "finance/report.html", context)


def _selected_partner(*, request, partners):
    selected_partner_id = request.GET.get("partner", "").strip()
    if not selected_partner_id:
        return partners[0] if partners else None
    try:
        selected_uuid = UUID(selected_partner_id)
    except (TypeError, ValueError) as exc:
        raise Http404(_("Partner not found.")) from exc
    for partner in partners:
        if partner.id == selected_uuid:
            return partner
    raise Http404(_("Partner not found."))


def _selected_period(*, request) -> str:
    period = request.GET.get("period", FINANCE_PERIOD_MONTH).strip()
    if period not in FINANCE_PERIODS:
        raise Http404(_("Finance period not found."))
    return period


def _selected_date(*, request, partner, now) -> date:
    raw_date = request.GET.get("date", "").strip()
    if raw_date:
        try:
            return date.fromisoformat(raw_date)
        except ValueError as exc:
            raise Http404(_("Finance report date not found.")) from exc
    if partner is None:
        return now.date()
    return now.astimezone(get_zone(partner.timezone)).date()


def _report_url(*, partner_id, period: str, anchor_date: date) -> str:
    query = urlencode(
        {
            "partner": partner_id,
            "period": period,
            "date": anchor_date.isoformat(),
        }
    )
    return f"{reverse('finance:report')}?{query}"
