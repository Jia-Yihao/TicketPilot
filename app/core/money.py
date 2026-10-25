from __future__ import annotations

from decimal import Decimal, InvalidOperation


def format_cents(cents: int) -> str:
    return f"{Decimal(cents) / Decimal(100):.2f}"


def parse_yuan_to_cents(value: str) -> int | None:
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    exponent = amount.as_tuple().exponent
    if not isinstance(exponent, int) or amount <= 0 or exponent < -2:
        return None
    cents = amount * 100
    if cents != cents.to_integral_value():
        return None
    return int(cents)
