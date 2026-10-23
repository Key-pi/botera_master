from django import template

from core.money import Money

register = template.Library()


@register.filter
def money_amount(value: int) -> str:
    """Render integer minor units as a fixed two-decimal major amount."""

    try:
        amount = int(value)
    except (TypeError, ValueError):
        return ""
    return str(Money(amount=amount).to_major_decimal())
