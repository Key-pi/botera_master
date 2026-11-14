from datetime import date

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.finance.selectors import (
    FINANCE_PERIOD_DAY,
    FINANCE_PERIOD_MONTH,
    FINANCE_PERIOD_WEEK,
    finance_period_window,
    shift_finance_period_anchor,
)
from apps.finance.services import create_expense
from apps.partners.services import create_partner_with_owner


@pytest.mark.parametrize(
    ("period", "anchor_date", "expected_start", "expected_end"),
    [
        (
            FINANCE_PERIOD_DAY,
            date(2026, 10, 24),
            date(2026, 10, 24),
            date(2026, 10, 24),
        ),
        (
            FINANCE_PERIOD_WEEK,
            date(2026, 10, 24),
            date(2026, 10, 19),
            date(2026, 10, 25),
        ),
        (
            FINANCE_PERIOD_MONTH,
            date(2024, 2, 15),
            date(2024, 2, 1),
            date(2024, 2, 29),
        ),
    ],
)
def test_finance_period_window_resolves_local_date_boundaries(
    period,
    anchor_date,
    expected_start,
    expected_end,
) -> None:
    window = finance_period_window(period=period, anchor_date=anchor_date)

    assert window.period == period
    assert window.anchor_date == anchor_date
    assert window.start_date == expected_start
    assert window.end_date == expected_end


def test_shift_finance_period_anchor_moves_by_selected_period() -> None:
    anchor_date = date(2026, 1, 31)

    assert shift_finance_period_anchor(
        period=FINANCE_PERIOD_DAY,
        anchor_date=anchor_date,
        steps=-1,
    ) == date(2026, 1, 30)
    assert shift_finance_period_anchor(
        period=FINANCE_PERIOD_WEEK,
        anchor_date=anchor_date,
        steps=1,
    ) == date(2026, 2, 7)
    assert shift_finance_period_anchor(
        period=FINANCE_PERIOD_MONTH,
        anchor_date=anchor_date,
        steps=1,
    ) == date(2026, 2, 1)


@pytest.mark.django_db
def test_finance_report_is_tenant_scoped_and_uses_major_unit_display(client) -> None:
    superuser = User.objects.create_superuser(username="root", password="secret")
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Finance Report Partner",
        owner_username="finance-report-owner",
        owner_password="secret",
    )
    foreign = create_partner_with_owner(
        actor=superuser,
        partner_name="Foreign Finance Report Partner",
        owner_username="foreign-finance-report-owner",
        owner_password="secret",
    )
    own_expense = create_expense(
        actor=created.owner,
        partner=created.partner,
        date=date(2026, 10, 24),
        amount=10_050,
        description="Workspace supplies",
    )
    create_expense(
        actor=foreign.owner,
        partner=foreign.partner,
        date=date(2026, 10, 24),
        amount=99_999,
        description="Foreign private expense",
    )
    client.force_login(created.owner)
    report_url = reverse("finance:report")

    response = client.get(
        report_url,
        {
            "partner": str(created.partner.id),
            "period": FINANCE_PERIOD_MONTH,
            "date": "2026-10-24",
        },
    )

    assert response.status_code == 200
    assert response.context["selected_partner"] == created.partner
    assert response.context["window"].start_date == date(2026, 10, 1)
    assert response.context["window"].end_date == date(2026, 10, 31)
    assert response.context["report"].expense_amount == 10_050
    assert response.context["report"].net_actual_amount == -10_050
    assert tuple(response.context["expenses"]) == (own_expense,)
    content = response.content.decode()
    assert "Workspace supplies" in content
    assert "100.50" in content
    assert "Foreign private expense" not in content

    foreign_response = client.get(
        report_url,
        {
            "partner": str(foreign.partner.id),
            "period": FINANCE_PERIOD_MONTH,
            "date": "2026-10-24",
        },
    )

    assert foreign_response.status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize(
    "query",
    [
        {"period": "quarter"},
        {"date": "not-a-date"},
        {"partner": "not-a-uuid"},
    ],
)
def test_finance_report_rejects_invalid_navigation_values(client, query) -> None:
    superuser = User.objects.create_superuser(username="root", password="secret")
    created = create_partner_with_owner(
        actor=superuser,
        partner_name="Finance Invalid Query Partner",
        owner_username=f"finance-invalid-{next(iter(query))}",
        owner_password="secret",
    )
    client.force_login(created.owner)
    params = {"partner": str(created.partner.id), **query}

    response = client.get(reverse("finance:report"), params)

    assert response.status_code == 404
