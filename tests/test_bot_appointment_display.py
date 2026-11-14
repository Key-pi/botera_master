from types import SimpleNamespace

from apps.bot.appointment_display import (
    MAX_APPOINTMENT_SERVICE_DETAILS_LENGTH,
    MAX_TELEGRAM_APPOINTMENT_DETAILS_LENGTH,
    appointment_service_details,
    telegram_safe_appointment_text,
)


def test_appointment_service_details_stays_within_message_budget():
    items = [
        SimpleNamespace(
            service_name_snapshot=f"Service {index} " + ("x" * 240),
            price_amount=10_000 + index,
            currency="UAH",
            duration_minutes=60,
            is_overridden=False,
        )
        for index in range(20)
    ]
    appointment = SimpleNamespace(
        service_items=SimpleNamespace(all=lambda: items),
    )

    rendered = appointment_service_details(
        appointment=appointment,
        language="en",
    )

    assert len(rendered) <= MAX_APPOINTMENT_SERVICE_DETAILS_LENGTH
    assert "more service(s)" in rendered


def test_appointment_detail_text_stays_below_telegram_limit():
    rendered = telegram_safe_appointment_text(
        "Appointment details\n" + ("x" * 5_000),
    )

    assert len(rendered) == MAX_TELEGRAM_APPOINTMENT_DETAILS_LENGTH
    assert rendered.endswith("\n...")
