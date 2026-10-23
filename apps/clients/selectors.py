from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.db.models import QuerySet, Sum
from django.utils import timezone

from apps.accounts.selectors import partner_ids_for_user
from apps.appointments.models import Appointment
from apps.clients.models import ClientProfile, TelegramIdentity
from apps.clients.phone import normalize_phone
from apps.finance.models import Penalty
from core.money import Money

if TYPE_CHECKING:
    from datetime import datetime

    from apps.accounts.models import User
    from apps.partners.models import Partner


@dataclass(frozen=True, slots=True)
class ClientOperationalSummary:
    """Computed client-card totals without duplicating business data."""

    completed_visit_count: int
    future_appointment_count: int
    total_spent: tuple[Money, ...]
    outstanding_penalty_count: int
    outstanding_penalty_totals: tuple[Money, ...]


def telegram_identity_by_user_id(telegram_user_id: int) -> TelegramIdentity | None:
    """Return a Telegram identity by Telegram's stable user id."""

    return TelegramIdentity.objects.filter(telegram_user_id=telegram_user_id).first()


def client_profiles_for_partner(partner: Partner) -> QuerySet[ClientProfile]:
    """Return all client profiles for one partner, including manual profiles."""

    return ClientProfile.objects.filter(partner=partner).select_related(
        "partner",
        "telegram_identity",
    )


def client_profiles_visible_to_user(user: User) -> QuerySet[ClientProfile]:
    """Return client profiles an admin actor can see through trusted memberships."""

    queryset = ClientProfile.objects.select_related("partner", "telegram_identity")
    if user.is_superuser:
        return queryset
    return queryset.filter(partner_id__in=partner_ids_for_user(user))


def client_profile_for_telegram_identity(
    *,
    partner: Partner,
    telegram_identity: TelegramIdentity,
) -> ClientProfile | None:
    """Return the partner profile for a shared Telegram identity if it exists."""

    return (
        ClientProfile.objects.filter(
            partner=partner,
            telegram_identity=telegram_identity,
        )
        .select_related("partner", "telegram_identity")
        .first()
    )


def manual_client_link_candidates(
    *,
    partner: Partner,
    phone: str,
    exclude_profile: ClientProfile | None = None,
) -> QuerySet[ClientProfile]:
    """Suggest manual clients with the same phone; never auto-merge them."""

    normalized_phone = normalize_phone(phone)
    if not normalized_phone:
        return ClientProfile.objects.none()

    queryset = client_profiles_for_partner(partner).filter(
        telegram_identity__isnull=True,
        phone=normalized_phone,
    )
    if exclude_profile is not None:
        queryset = queryset.exclude(id=exclude_profile.id)
    return queryset


def client_operational_summary(
    *,
    client_profile: ClientProfile,
    now: datetime | None = None,
) -> ClientOperationalSummary:
    """Return the compact business summary used by the partner client card."""

    current_time = now or timezone.now()
    appointments = Appointment.objects.filter(
        partner=client_profile.partner,
        client_profile=client_profile,
    )
    completed = appointments.filter(status=Appointment.Status.COMPLETED)
    future = appointments.filter(
        status__in=[Appointment.Status.REQUESTED, Appointment.Status.CONFIRMED],
        start_at__gte=current_time,
    )
    penalties = Penalty.objects.filter(
        partner=client_profile.partner,
        client_profile=client_profile,
    )
    outstanding_penalties = penalties.filter(
        status__in=[Penalty.Status.UNPAID, Penalty.Status.APPLIED_TO_APPOINTMENT]
    )

    spent_by_currency = _amounts_by_currency(
        completed,
        amount_field="service_total_amount",
    )
    for currency, amount in _amounts_by_currency(
        penalties.filter(status=Penalty.Status.PAID),
        amount_field="amount",
    ).items():
        spent_by_currency[currency] = spent_by_currency.get(currency, 0) + amount

    return ClientOperationalSummary(
        completed_visit_count=completed.count(),
        future_appointment_count=future.count(),
        total_spent=_money_tuple(spent_by_currency),
        outstanding_penalty_count=outstanding_penalties.count(),
        outstanding_penalty_totals=_money_tuple(
            _amounts_by_currency(outstanding_penalties, amount_field="amount")
        ),
    )


def _amounts_by_currency(queryset: QuerySet, *, amount_field: str) -> dict[str, int]:
    rows = queryset.values("currency").annotate(total=Sum(amount_field)).order_by("currency")
    return {row["currency"]: row["total"] or 0 for row in rows}


def _money_tuple(amounts_by_currency: dict[str, int]) -> tuple[Money, ...]:
    return tuple(
        Money(amount=amount, currency=currency)
        for currency, amount in sorted(amounts_by_currency.items())
    )
