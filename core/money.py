from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


@dataclass(frozen=True, slots=True)
class Money:
    amount: int
    currency: str = "UAH"

    @classmethod
    def from_major(cls, value: str | Decimal, currency: str = "UAH") -> Money:
        decimal_value = Decimal(value)
        minor_units = int((decimal_value * Decimal("100")).quantize(Decimal("1"), ROUND_HALF_UP))
        return cls(amount=minor_units, currency=currency)

    def to_major_decimal(self) -> Decimal:
        return (Decimal(self.amount) / Decimal("100")).quantize(Decimal("0.01"))
