"""Пари и количества: само `Decimal`, `ROUND_HALF_UP` (docs/data-model.md §0)."""

from decimal import ROUND_HALF_UP, Decimal

_CENT = Decimal("0.01")
_MILLI = Decimal("0.001")
_HUNDRED = Decimal(100)


def q2(value: Decimal) -> Decimal:
    """До 0.01 (суми в евро)."""
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def q3(value: Decimal) -> Decimal:
    """До 0.001 (тонове)."""
    return value.quantize(_MILLI, rounding=ROUND_HALF_UP)


def line_amount(qty: Decimal, price: Decimal) -> Decimal:
    """Ред = количество × цена, закръглено до 0.01 на ред."""
    return q2(qty * price)


def vat(amount: Decimal, rate: Decimal) -> Decimal:
    """ДДС = round(сума без ДДС × ставка, 2); `rate` е в проценти (20.00)."""
    return q2(amount * rate / _HUNDRED)
