from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from datetime import timezone as datetime_timezone
from typing import TYPE_CHECKING

from django.db.models import QuerySet, Sum

from apps.appointments.models import Appointment
from apps.catalog.models import DEFAULT_CURRENCY
from apps.finance.models import Expense, Penalty, PenaltyApplication
from core.timezones import get_zone

if TYPE_CHECKING:
    from apps.clients.models import ClientProfile
    from apps.partners.models import Partner


@dataclass(frozen=True)
class FinanceReport:
    """Lightweight partner finance totals for one local-date period."""

    period_start: date
    period_end: date
    currency: str
    actual_service_revenue_amount: int
    actual_penalty_revenue_amount: int
    actual_revenue_amount: int
    expected_service_revenue_amount: int
    expected_penalty_revenue_amount: int
    expected_revenue_amount: int
    expense_amount: int
    net_actual_amount: int


def penalties_for_client(client_profile: ClientProfile) -> QuerySet[Penalty]:
    """Return all penalties for one partner-scoped client."""

    return Penalty.objects.filter(client_profile=client_profile).select_related(
        "partner",
        "client_profile",
        "source_appointment",
    )


def active_unpaid_penalties_for_client(client_profile: ClientProfile) -> QuerySet[Penalty]:
    """Return penalties visible as active unpaid debt for a client."""

    return penalties_for_client(client_profile).filter(status=Penalty.Status.UNPAID)


def expenses_for_partner(
    *,
    partner: Partner,
    start_date: date | None = None,
    end_date: date | None = None,
) -> QuerySet[Expense]:
    """Return partner expenses, optionally restricted to local dates."""

    queryset = Expense.objects.filter(partner=partner).select_related("partner", "created_by")
    if start_date is not None:
        queryset = queryset.filter(date__gte=start_date)
    if end_date is not None:
        queryset = queryset.filter(date__lte=end_date)
    return queryset


def finance_report_for_partner(
    *,
    partner: Partner,
    start_date: date,
    end_date: date,
    currency: str = DEFAULT_CURRENCY,
    now: datetime | None = None,
) -> FinanceReport:
    """Build actual/expected revenue and expense totals using partner local dates."""

    if start_date > end_date:
        raise ValueError("start_date must be before or equal to end_date.")
    now = now or datetime.now(tz=datetime_timezone.utc)
    start_at, end_at = _local_date_bounds(partner=partner, start_date=start_date, end_date=end_date)

    completed_appointments = Appointment.objects.filter(
        partner=partner,
        status=Appointment.Status.COMPLETED,
        start_at__gte=start_at,
        start_at__lt=end_at,
        currency=currency,
    )
    actual_service_revenue = _sum(completed_appointments, "service_total_amount")
    actual_penalty_revenue = _sum(
        PenaltyApplication.objects.filter(
            partner=partner,
            appointment__status=Appointment.Status.COMPLETED,
            appointment__start_at__gte=start_at,
            appointment__start_at__lt=end_at,
            status=PenaltyApplication.Status.SETTLED,
            currency=currency,
        ),
        "amount_snapshot",
    )

    expected_appointments = Appointment.objects.filter(
        partner=partner,
        status=Appointment.Status.CONFIRMED,
        start_at__gte=max(start_at, now),
        start_at__lt=end_at,
        currency=currency,
    )
    expected_service_revenue = _sum(expected_appointments, "service_total_amount")
    expected_penalty_revenue = _sum(
        PenaltyApplication.objects.filter(
            partner=partner,
            appointment__status=Appointment.Status.CONFIRMED,
            appointment__start_at__gte=max(start_at, now),
            appointment__start_at__lt=end_at,
            status=PenaltyApplication.Status.ACTIVE,
            currency=currency,
        ),
        "amount_snapshot",
    )
    expense_amount = _sum(
        expenses_for_partner(partner=partner, start_date=start_date, end_date=end_date).filter(
            currency=currency
        ),
        "amount",
    )

    actual_revenue = actual_service_revenue + actual_penalty_revenue
    expected_revenue = expected_service_revenue + expected_penalty_revenue
    return FinanceReport(
        period_start=start_date,
        period_end=end_date,
        currency=currency,
        actual_service_revenue_amount=actual_service_revenue,
        actual_penalty_revenue_amount=actual_penalty_revenue,
        actual_revenue_amount=actual_revenue,
        expected_service_revenue_amount=expected_service_revenue,
        expected_penalty_revenue_amount=expected_penalty_revenue,
        expected_revenue_amount=expected_revenue,
        expense_amount=expense_amount,
        net_actual_amount=actual_revenue - expense_amount,
    )


def _local_date_bounds(
    *,
    partner: Partner,
    start_date: date,
    end_date: date,
) -> tuple[datetime, datetime]:
    zone = get_zone(partner.timezone)
    local_start = datetime.combine(start_date, time.min, tzinfo=zone)
    local_end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=zone)
    return (
        local_start.astimezone(datetime_timezone.utc),
        local_end.astimezone(datetime_timezone.utc),
    )


def _sum(queryset: QuerySet, field_name: str) -> int:
    return queryset.aggregate(total=Sum(field_name))["total"] or 0
