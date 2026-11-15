"""Изчислими показатели на договора (docs/data-model.md §6) — не се пазят в базата.

Курсовете (`trips`) още нямат таблица (agro-l2v.7): докато я няма, `_trip_totals` връща празно и
изтеглено = `opening_drawn_qty_t`, заявено = 0. Дописва се в T1.7 с една GROUP BY заявка.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from sqlalchemy.orm import Session

from app.models.contracts import Contract

ZERO = Decimal("0.000")


class ContractState(StrEnum):
    OK = "ok"
    OVER = "over"  # „Не стигат X т“
    OPEN = "open"  # без договорено количество


@dataclass(frozen=True)
class ContractMetrics:
    drawn_t: Decimal
    requested_t: Decimal
    remaining_t: Decimal | None  # None при state=open; отрицателно при state=over
    state: ContractState


@dataclass(frozen=True)
class TripTotals:
    completed_actual_t: Decimal = ZERO  # SUM(actual_qty_t) за completed
    planned_t: Decimal = ZERO  # SUM(planned_qty_t) за planned


def compute_metrics(
    contracted_qty_t: Decimal | None, opening_drawn_qty_t: Decimal, totals: TripTotals
) -> ContractMetrics:
    drawn = opening_drawn_qty_t + totals.completed_actual_t
    requested = totals.planned_t
    if contracted_qty_t is None:
        return ContractMetrics(drawn, requested, None, ContractState.OPEN)
    remaining = contracted_qty_t - drawn - requested
    state = ContractState.OVER if remaining < 0 else ContractState.OK
    return ContractMetrics(drawn, requested, remaining, state)


def _trip_totals(session: Session, contracts: Iterable[Contract]) -> Mapping[int, TripTotals]:
    """Курсовете по договорите с ЕДНА заявка (GROUP BY). Таблицата `trips` идва с agro-l2v.7."""
    return {}


def metrics_for(session: Session, contracts: Iterable[Contract]) -> dict[int, ContractMetrics]:
    """Показатели за много договори наведнъж (без N+1); ключ е `contract.id`."""
    items = list(contracts)
    totals = _trip_totals(session, items)
    return {
        c.id: compute_metrics(c.contracted_qty_t, c.opening_drawn_qty_t, totals.get(c.id, TripTotals()))
        for c in items
    }


def metrics(session: Session, contract: Contract) -> ContractMetrics:
    return metrics_for(session, [contract])[contract.id]
