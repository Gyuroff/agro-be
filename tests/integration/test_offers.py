"""Оферти по най-ниска цена на разфасовка (agro-2lg)."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit_context import AuditContext
from app.models.contracts import Contract, ContractPrice
from app.models.masterdata import Counterparty, Product, Warehouse
from app.models.trips import Trip
from app.services.offers import best_offer

D = Decimal
DAY = date(2026, 11, 20)


class Market:
    def __init__(self, s: Session) -> None:
        self.s = s
        self.oliva = Counterparty(name="Олива", legal_name="Олива АД", eik="333333333")
        self.europack = Counterparty(name="Юропак", legal_name="Юропак АД", eik="444444444")
        self.pellets = Product(name="Пелети")
        s.add_all([self.oliva, self.europack, self.pellets])
        s.flush()
        self.razdelna = Warehouse(counterparty_id=self.oliva.id, name="Разделна", packagings=["bulk"])
        self.popovo = Warehouse(counterparty_id=self.europack.id, name="Попово")
        s.add_all([self.razdelna, self.popovo])
        s.flush()
        self.c_raz = self.contract("П-1", self.oliva, self.razdelna)
        self.c_pop = self.contract("П-2", self.europack, self.popovo)

    def contract(self, number: str, cp: Counterparty, wh: Warehouse, **kw: object) -> Contract:
        c = Contract(
            number=number,
            side="purchase",
            counterparty_id=cp.id,
            product_id=self.pellets.id,
            warehouse_id=wh.id,
            valid_from=date(2026, 1, 1),
            **kw,
        )
        self.s.add(c)
        self.s.flush()
        return c

    def price(self, c: Contract, packaging: str, value: str, frm: date = date(2026, 1, 1)) -> None:
        self.s.add(ContractPrice(contract_id=c.id, packaging=packaging, valid_from=frm, price_per_t=D(value)))
        self.s.flush()

    def best(self, packaging: str, **kw: int) -> tuple[int | None, list[int]]:
        r = best_offer(self.s, product_id=self.pellets.id, packaging=packaging, on=DAY, **kw)
        return (r.best.contract_id if r.best else None, [o.contract_id for o in r.candidates])


@pytest.fixture
def m(db_session: Session, audit_ctx: AuditContext) -> Market:
    return Market(db_session)


def test_cheapest_per_packaging(m: Market) -> None:
    m.price(m.c_raz, "bulk", "180")
    m.price(m.c_pop, "bulk", "175")
    m.price(m.c_pop, "bagged", "210")
    assert m.best("bulk") == (m.c_pop.id, [m.c_pop.id, m.c_raz.id])
    # Разделна не поддържа бегове — дори да има цена, не е кандидат
    m.price(m.c_raz, "bagged", "100")
    assert m.best("bagged") == (m.c_pop.id, [m.c_pop.id])


def test_price_history_uses_latest_valid(m: Market) -> None:
    m.price(m.c_raz, "bulk", "180")
    m.price(m.c_raz, "bulk", "150", date(2026, 12, 1))  # бъдеща цена не важи
    m.price(m.c_pop, "bulk", "170")
    assert m.best("bulk")[0] == m.c_pop.id
    m.price(m.c_raz, "bulk", "160", date(2026, 11, 1))
    assert m.best("bulk")[0] == m.c_raz.id


def test_ineligible_contracts_are_skipped(m: Market) -> None:
    m.price(m.c_raz, "bulk", "100")
    m.price(m.c_pop, "bulk", "170")
    m.c_raz.valid_to = date(2026, 11, 1)  # изтекъл
    m.s.flush()
    assert m.best("bulk")[0] == m.c_pop.id
    m.c_pop.status = "closed"
    m.s.flush()
    assert m.best("bulk") == (None, [])  # без оферта → „избери“, не грешка


def test_requested_warehouse_is_respected_and_not_overridden(m: Market) -> None:
    m.price(m.c_raz, "bulk", "180")
    m.price(m.c_pop, "bulk", "170")
    assert m.best("bulk", warehouse_id=m.razdelna.id)[0] == m.c_raz.id
    m.price(m.c_pop, "bagged", "200")
    assert m.best("bagged", warehouse_id=m.razdelna.id) == (None, [])


def test_warehouse_packagings_check(m: Market) -> None:
    m.razdelna.packagings = []
    with pytest.raises(IntegrityError):
        m.s.flush()


def test_planned_trip_without_supplier_is_allowed(m: Market) -> None:
    sale = Contract(
        number="2026-1", side="sale", counterparty_id=m.oliva.id, product_id=m.pellets.id,
        valid_from=date(2026, 1, 1),
    )  # fmt: skip
    m.s.add(sale)
    m.s.flush()
    kw: dict[str, object] = dict(  # noqa: C408
        origin="request", sale_contract_id=sale.id, product_id=m.pellets.id, planned_date=DAY,
        plate_norm="CA1234BX", plate_display="СА 1234 ВХ", planned_qty_t=D("25.2"),
    )  # fmt: skip
    m.s.add(Trip(status="planned", **kw))
    m.s.flush()
    m.s.add(Trip(status="cancelled", **kw))
    with pytest.raises(IntegrityError):
        m.s.flush()
