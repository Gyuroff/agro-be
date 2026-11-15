"""Изчислими показатели на договора (docs/data-model.md §6) — не се пазят в базата.

Изтеглено = `opening_drawn_qty_t` + SUM(actual_qty_t) на `completed`; заявено = SUM(planned_qty_t) на
`planned`; `cancelled` и `unplanned` не се броят.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.contracts import Contract
from app.models.trips import Trip, TripStatus

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
    """Курсовете по договорите с ЕДНА заявка (GROUP BY). Договорът е продажба или покупка."""
    ids = [c.id for c in contracts]
    if not ids:
        return {}
    done = func.coalesce(
        func.sum(case((Trip.status == TripStatus.COMPLETED.value, Trip.actual_qty_t), else_=0)), 0
    )
    planned = func.coalesce(
        func.sum(case((Trip.status == TripStatus.PLANNED.value, Trip.planned_qty_t), else_=0)), 0
    )
    totals: dict[int, TripTotals] = {}
    for column in (Trip.sale_contract_id, Trip.purchase_contract_id):
        rows = session.execute(select(column, done, planned).where(column.in_(ids)).group_by(column)).all()
        for contract_id, completed_t, planned_t in rows:
            if contract_id is not None:
                totals[contract_id] = TripTotals(Decimal(completed_t), Decimal(planned_t))
    return totals


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
