"""Карта „общ запас наближава“ (agro-2lg)."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit_context import AuditContext
from app.models.cards import ActionCard, CardKind
from app.models.contracts import Contract
from app.models.trips import Trip
from app.services.stock import check_stock_low
from tests.integration.test_offers import Market

D = Decimal
TODAY = date(2026, 11, 20)


@pytest.fixture
def m(db_session: Session, audit_ctx: AuditContext) -> Market:
    market = Market(db_session)
    market.c_raz.contracted_qty_t = D("1000")
    market.c_pop.contracted_qty_t = D("2000")
    db_session.flush()
    return market


def take(m: Market, contract: Contract, qty: str) -> None:
    sale = Contract(
        number=f"S-{contract.id}-{qty}", side="sale", counterparty_id=m.oliva.id, product_id=m.pellets.id,
        valid_from=date(2026, 1, 1),
    )  # fmt: skip
    m.s.add(sale)
    m.s.flush()
    m.s.add(
        Trip(
            status="planned", origin="request", sale_contract_id=sale.id, purchase_contract_id=contract.id,
            supplier_id=contract.counterparty_id, product_id=m.pellets.id, planned_date=TODAY,
            plate_norm="CA1234BX", plate_display="СА 1234 ВХ", planned_qty_t=D(qty),
        )
    )  # fmt: skip
    m.s.flush()


def test_no_card_above_threshold(m: Market) -> None:
    take(m, m.c_pop, "2000")  # остават 1000 от 3000 = 33%
    assert check_stock_low(m.s, m.pellets.id, TODAY) is None


def test_card_below_ten_percent_and_deduped(m: Market) -> None:
    take(m, m.c_raz, "1000")
    take(m, m.c_pop, "1750")  # остават 250 от 3000 = 8,33%
    card = check_stock_low(m.s, m.pellets.id, TODAY)
    assert card is not None and card.kind == CardKind.STOCK_LOW.value
    assert card.payload["remaining_t"] == "250.000" and card.payload["remaining_pct"] == "8.33"
    assert card.payload["contracts"] == 2 and card.payload["total_t"] == "3000.000"
    assert check_stock_low(m.s, m.pellets.id, TODAY) is card  # без втора карта
    assert len(m.s.scalars(select(ActionCard)).all()) == 1


def test_open_contracts_and_expired_ones_do_not_count(m: Market) -> None:
    m.c_pop.contracted_qty_t = None  # „open“ — не е в сбора
    m.c_raz.valid_to = date(2026, 6, 1)  # изтекъл
    m.s.flush()
    assert check_stock_low(m.s, m.pellets.id, TODAY) is None
