"""Следващият номер на фактура — винаги от счетоводния софтуер (Мега), не от `number_sequences`.

Решение на собственика: няма пробна поредица; ERP издава с номера, който Мега показва като следващ
свободен. Начинът за връзка с Мега е отворен въпрос 12, затова засега има само интерфейс и
`UnconfiguredNumberProvider` (гърми с 503); истинската реализация се закача през `set_number_provider`.
"""

from typing import Protocol

from app.core.errors import DomainError

NUMBER_WIDTH = 10


class InvoiceNumberProvider(Protocol):
    def next_number(self) -> int:
        """Следващият свободен номер в Мега. Не го резервира — резервира го издаването."""
        ...


class UnconfiguredNumberProvider:
    def next_number(self) -> int:
        raise DomainError(
            "mega_number_unavailable",
            "Няма връзка с Мега за следващия номер на фактура.",
            http_status=503,
        )


_provider: InvoiceNumberProvider = UnconfiguredNumberProvider()


def get_number_provider() -> InvoiceNumberProvider:
    return _provider


def set_number_provider(provider: InvoiceNumberProvider) -> None:
    global _provider
    _provider = provider


def format_invoice_number(number: int) -> str:
    return str(number).zfill(NUMBER_WIDTH)
