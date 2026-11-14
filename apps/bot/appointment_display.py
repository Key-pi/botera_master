from __future__ import annotations

from apps.appointments.models import Appointment
from core.money import Money

MAX_APPOINTMENT_SERVICE_DETAILS_LENGTH = 2_200
MAX_TELEGRAM_APPOINTMENT_DETAILS_LENGTH = 3_900


def appointment_service_details(
    *,
    appointment: Appointment,
    language: str,
    max_length: int = MAX_APPOINTMENT_SERVICE_DETAILS_LENGTH,
) -> str:
    """Render snapshotted service lines without exceeding a Telegram-safe budget."""

    lines = [
        _service_line(
            name=item.service_name_snapshot,
            amount=item.price_amount,
            currency=item.currency,
            duration_minutes=item.duration_minutes,
            is_overridden=item.is_overridden,
            language=language,
        )
        for item in appointment.service_items.all()
    ]
    if not lines:
        return "-"

    visible: list[str] = []
    for line in lines:
        candidate = "\n".join([*visible, line])
        if len(candidate) > max_length:
            break
        visible.append(line)

    hidden_count = len(lines) - len(visible)
    if hidden_count:
        while visible:
            marker = _hidden_services_label(hidden_count, language)
            if len("\n".join([*visible, marker])) <= max_length:
                break
            visible.pop()
            hidden_count += 1
        marker = _hidden_services_label(hidden_count, language)
        visible.append(marker[:max_length])

    return "\n".join(visible)


def telegram_safe_appointment_text(
    text: str,
    *,
    max_length: int = MAX_TELEGRAM_APPOINTMENT_DETAILS_LENGTH,
) -> str:
    """Keep one appointment detail response below Telegram's message limit."""

    if len(text) <= max_length:
        return text
    suffix = "\n..."
    return f"{text[: max_length - len(suffix)].rstrip()}{suffix}"


def _service_line(
    *,
    name: str,
    amount: int,
    currency: str,
    duration_minutes: int,
    is_overridden: bool,
    language: str,
) -> str:
    price = Money(amount=amount, currency=currency).to_major_decimal()
    override = f" ({_label('overridden', language)})" if is_overridden else ""
    return (
        f"- {name}: {price} {currency}, {duration_minutes} "
        f"{_label('minutes', language)}{override}"
    )


def _hidden_services_label(count: int, language: str) -> str:
    labels = {
        "uk": f"... ще послуг: {count}",
        "ru": f"... ещё услуг: {count}",
        "en": f"... {count} more service(s)",
    }
    return labels.get(language, labels["uk"])


def _label(key: str, language: str) -> str:
    labels = {
        "minutes": {"uk": "хв", "ru": "мин", "en": "min"},
        "overridden": {
            "uk": "змінено майстром",
            "ru": "изменено мастером",
            "en": "changed by master",
        },
    }
    return labels[key].get(language, labels[key]["uk"])
