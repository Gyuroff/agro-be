"""Оферти (agro-2lg): за ред на заявка — най-евтината оферта сред ВСИЧКИ допустими покупни договори.

Код = допустимост и аритметика. Критерий е само цената; други фактори преценя човекът и сменя ръчно.
Няма допустима оферта → `best=None` (редът е „избери“, заявката не се блокира).
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.contracts import Contract, ContractPrice, ContractSide, ContractStatus
from app.models.masterdata import Warehouse


@dataclass(frozen=True)
class Offer:
    contract_id: int
    warehouse_id: int
    supplier_id: int
    price_per_t: Decimal

    def as_json(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "warehouse_id": self.warehouse_id,
            "price_per_t": str(self.price_per_t),
        }


@dataclass(frozen=True)
class OfferResult:
    best: Offer | None
    candidates: list[Offer]

    def note(self) -> dict[str, Any]:
        return {
            "candidates": [o.as_json() for o in self.candidates],
            "chosen": self.best.as_json() if self.best else None,
        }


def eligible_offers(
    session: Session,
    *,
    product_id: int,
    packaging: str,
    on: date,
    warehouse_id: int | None = None,
) -> list[Offer]:
    """Активни покупни договори към `on`, чийто склад поддържа разфасовката и има цена за нея."""
    stmt = (
        select(Contract, Warehouse)
        .join(Warehouse, Warehouse.id == Contract.warehouse_id)
        .where(
            Contract.side == ContractSide.PURCHASE.value,
            Contract.status == ContractStatus.ACTIVE.value,
            Contract.product_id == product_id,
            Contract.valid_from <= on,
            or_(Contract.valid_to.is_(None), Contract.valid_to >= on),
            Warehouse.is_active.is_(True),
            Warehouse.packagings.contains([packaging]),
        )
        .order_by(Contract.id)
    )
    if warehouse_id is not None:
        stmt = stmt.where(Warehouse.id == warehouse_id)
    offers: list[Offer] = []
    for contract, warehouse in session.execute(stmt):
        price = session.scalar(
            select(ContractPrice.price_per_t)
            .where(
                ContractPrice.contract_id == contract.id,
                ContractPrice.packaging == packaging,
                ContractPrice.valid_from <= on,
            )
            .order_by(ContractPrice.valid_from.desc())
            .limit(1)
        )
        if price is not None:
            offers.append(Offer(contract.id, warehouse.id, contract.counterparty_id, price))
    return offers


def best_offer(
    session: Session,
    *,
    product_id: int,
    packaging: str,
    on: date,
    warehouse_id: int | None = None,
) -> OfferResult:
    """Най-ниска цена; при равенство — по-малкият договор. Ако „Място на товарене“ няма оферта — без него."""
    offers = eligible_offers(
        session, product_id=product_id, packaging=packaging, on=on, warehouse_id=warehouse_id
    )
    ranked = sorted(offers, key=lambda o: (o.price_per_t, o.contract_id))
    return OfferResult(best=ranked[0] if ranked else None, candidates=ranked)
