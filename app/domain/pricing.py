"""Цена по договор към дата (docs/data-model.md §6): важи редът с най-голямо `valid_from <= d`."""

from collections.abc import Iterable
from datetime import date
from decimal import Decimal

from app.core.errors import DomainError


class NoPriceForDate(DomainError):
    """Няма цена по договора за датата на натоварване — курсът не може да се потвърди."""

    def __init__(self, contract_id: int, on: date) -> None:
        super().__init__(
            "no_price_for_date",
            f"Няма цена по договора за {on.strftime('%d.%m.%Y')}.",
            http_status=422,
        )
        self.contract_id = contract_id
        self.on = on


def pick_price(contract_id: int, prices: Iterable[tuple[date, Decimal]], on: date) -> Decimal:
    """Цената с най-късно `valid_from <= on`; `NoPriceForDate`, ако няма такава."""
    best: tuple[date, Decimal] | None = None
    for valid_from, price in prices:
        if valid_from <= on and (best is None or valid_from > best[0]):
            best = (valid_from, price)
    if best is None:
        raise NoPriceForDate(contract_id, on)
    return best[1]
