"""contracts/contract_prices, валидации, price_at, показатели (agro-l2v.5)."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit_context import AuditContext
from app.domain.pricing import NoPriceForDate
from app.models.contracts import Contract, ContractPrice
from app.models.masterdata import Contact, Counterparty, Product, Warehouse
from app.services.contract_metrics import ContractState, metrics, metrics_for
from app.services.contracts import ContractValidationError, price_at, validate_contract

D = Decimal


class World:
    def __init__(self, session: Session) -> None:
        self.client = Counterparty(name="Клиент", legal_name="Клиент ООД", eik="111111111")
        self.supplier = Counterparty(name="Доставчик", legal_name="Доставчик АД", eik="222222222")
        self.pellets = Product(name="Пелети")
        self.npk = Product(name="Тор NPK")
        session.add_all([self.client, self.supplier, self.pellets, self.npk])
        session.flush()
        self.wh = Warehouse(counterparty_id=self.supplier.id, name="Тервел")
        self.orders = Contact(
            counterparty_id=self.supplier.id, email="orders@peletad.bg", receives_orders=True
        )
        self.other = Contact(counterparty_id=self.supplier.id, email="acc@peletad.bg")
        self.foreign = Contact(counterparty_id=self.client.id, email="x@klient.bg", receives_orders=True)
        session.add_all([self.wh, self.orders, self.other, self.foreign])
        session.flush()
        self.session = session

    def purchase(self, number: str = "П-2026-007", **kw: Any) -> Contract:
        base: dict[str, object] = {
            "number": number,
            "side": "purchase",
            "counterparty_id": self.supplier.id,
            "product_id": self.pellets.id,
            "warehouse_id": self.wh.id,
            "valid_from": date(2026, 1, 1),
        }
        c = Contract(**{**base, **kw})
        self.session.add(c)
        self.session.flush()
        return c

    def sale(self, number: str = "2026-018", **kw: Any) -> Contract:
        base: dict[str, object] = {
            "number": number,
            "side": "sale",
            "counterparty_id": self.client.id,
            "product_id": self.pellets.id,
            "valid_from": date(2026, 1, 1),
        }
        c = Contract(**{**base, **kw})
        self.session.add(c)
        self.session.flush()
        return c


@pytest.fixture
def w(db_session: Session, audit_ctx: AuditContext) -> World:
    return World(db_session)


def test_defaults(w: World, db_session: Session) -> None:
    c = w.sale()
    db_session.refresh(c)
    assert (c.opening_drawn_qty_t, c.planned_truck_qty_t, c.status, c.tracks_customer_credit) == (
        D("0"),
        D("25.200"),
        "active",
        False,
    )
    assert c.contracted_qty_t is None


# --- CHECK-ове ---


def test_purchase_requires_warehouse(w: World, db_session: Session) -> None:
    with pytest.raises(IntegrityError):
        w.purchase(warehouse_id=None)


def test_sale_rejects_warehouse(w: World) -> None:
    with pytest.raises(IntegrityError):
        w.sale(warehouse_id=w.wh.id)


def test_supply_contract_only_on_sale(w: World) -> None:
    p = w.purchase()
    with pytest.raises(IntegrityError):
        w.purchase("П-2", supply_contract_id=p.id)


@pytest.mark.parametrize(
    "extra",
    [{"billing_mode": "per_trip"}, {"billing_rrule": "FREQ=WEEKLY"}, {"billing_dtstart": date(2026, 1, 1)}],
)
def test_billing_only_on_sale(w: World, extra: dict[str, Any]) -> None:
    with pytest.raises(IntegrityError):
        w.purchase(**extra)


def test_billing_on_sale_ok(w: World) -> None:
    w.sale(billing_mode="per_period", billing_rrule="FREQ=WEEKLY;BYDAY=MO", billing_dtstart=date(2026, 1, 5))


def test_sale_supply_contract_ok(w: World) -> None:
    p = w.purchase()
    w.sale(supply_contract_id=p.id)


def test_order_contact_and_payment_mode_only_on_purchase(w: World) -> None:
    with pytest.raises(IntegrityError):
        w.sale(order_contact_id=w.orders.id)


def test_payment_mode_only_on_purchase(w: World) -> None:
    with pytest.raises(IntegrityError):
        w.sale(purchase_payment_mode="prepaid")


def test_credit_only_on_sale(w: World) -> None:
    with pytest.raises(IntegrityError):
        w.purchase(tracks_customer_credit=True)


@pytest.mark.parametrize(
    "kw",
    [
        {"purchase_payment_mode": "cash"},
        {"funds_held_by": "bank"},
        {"advance_buffer_type": "days"},
        {"status": "deleted"},
    ],
)
def test_enum_checks(w: World, kw: dict[str, Any]) -> None:
    with pytest.raises(IntegrityError):
        w.purchase(**kw)


def test_number_unique(w: World) -> None:
    w.purchase("П-1")
    with pytest.raises(IntegrityError):
        w.purchase("П-1")


def test_price_must_be_positive(w: World, db_session: Session) -> None:
    c = w.sale()
    db_session.add(ContractPrice(contract_id=c.id, valid_from=date(2026, 1, 1), price_per_t=D("0")))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_price_unique_per_date(w: World, db_session: Session) -> None:
    c = w.sale()
    db_session.add(ContractPrice(contract_id=c.id, valid_from=date(2026, 1, 1), price_per_t=D("100")))
    db_session.flush()
    db_session.add(ContractPrice(contract_id=c.id, valid_from=date(2026, 1, 1), price_per_t=D("110")))
    with pytest.raises(IntegrityError):
        db_session.flush()


# --- price_at ---


def _two_prices(w: World, db_session: Session) -> Contract:
    c = w.sale()
    db_session.add_all(
        [
            ContractPrice(contract_id=c.id, valid_from=date(2026, 1, 1), price_per_t=D("250.00")),
            ContractPrice(contract_id=c.id, valid_from=date(2026, 11, 1), price_per_t=D("262.50")),
        ]
    )
    db_session.flush()
    return c


def test_price_at_boundary(w: World, db_session: Session) -> None:
    c = _two_prices(w, db_session)
    assert price_at(db_session, c.id, date(2026, 11, 1)) == D("262.50")
    assert price_at(db_session, c.id, date(2026, 10, 31)) == D("250.00")
    assert price_at(db_session, c.id, date(2027, 6, 1)) == D("262.50")


def test_price_at_before_first_price(w: World, db_session: Session) -> None:
    c = _two_prices(w, db_session)
    with pytest.raises(NoPriceForDate) as err:
        price_at(db_session, c.id, date(2025, 12, 31))
    assert (err.value.code, err.value.http_status) == ("no_price_for_date", 422)


def test_price_at_no_prices(w: World, db_session: Session) -> None:
    c = w.sale()
    with pytest.raises(NoPriceForDate):
        price_at(db_session, c.id, date(2026, 11, 1))


def test_price_at_other_contract_not_used(w: World, db_session: Session) -> None:
    c = _two_prices(w, db_session)
    other = w.sale("2026-019")
    with pytest.raises(NoPriceForDate):
        price_at(db_session, other.id, date(2026, 11, 1))
    assert price_at(db_session, c.id, date(2026, 11, 1)) == D("262.50")


# --- validate_contract ---


def test_supply_contract_other_product_rejected(w: World, db_session: Session) -> None:
    p = w.purchase()
    sale = Contract(
        number="2026-1",
        side="sale",
        counterparty_id=w.client.id,
        product_id=w.npk.id,
        supply_contract_id=p.id,
        valid_from=date(2026, 1, 1),
    )
    with pytest.raises(ContractValidationError) as err:
        validate_contract(db_session, sale)
    assert err.value.field == "supply_contract_id"


def test_supply_contract_must_be_purchase(w: World, db_session: Session) -> None:
    other_sale = w.sale("2026-2")
    sale = Contract(
        number="2026-3",
        side="sale",
        counterparty_id=w.client.id,
        product_id=w.pellets.id,
        supply_contract_id=other_sale.id,
        valid_from=date(2026, 1, 1),
    )
    with pytest.raises(ContractValidationError):
        validate_contract(db_session, sale)


def test_supply_contract_same_product_ok(w: World, db_session: Session) -> None:
    p = w.purchase()
    validate_contract(db_session, w.sale(supply_contract_id=p.id))


def test_order_contact_checks(w: World, db_session: Session) -> None:
    validate_contract(db_session, w.purchase("П-10", order_contact_id=w.orders.id))
    for contact in (w.foreign, w.other):  # чужд контрагент; без receives_orders
        c = w.purchase(f"П-{contact.id}", order_contact_id=contact.id)
        with pytest.raises(ContractValidationError) as err:
            validate_contract(db_session, c)
        assert err.value.field == "order_contact_id"


@pytest.mark.parametrize("rule", ["не е правило", "FREQ=SOMETIMES", "FREQ=WEEKLY;BYDAY=XX"])
def test_invalid_rrule(w: World, db_session: Session, rule: str) -> None:
    c = w.sale(billing_rrule=rule)
    with pytest.raises(ContractValidationError) as err:
        validate_contract(db_session, c)
    assert err.value.field == "billing_rrule"


def test_valid_rrule(w: World, db_session: Session) -> None:
    validate_contract(db_session, w.sale(billing_rrule="FREQ=DAILY;INTERVAL=7"))


# --- показатели (без курсове) ---


def test_metrics_open_contract(w: World, db_session: Session) -> None:
    m = metrics(db_session, w.sale())
    assert (m.state, m.remaining_t, m.drawn_t, m.requested_t) == (ContractState.OPEN, None, D("0"), D("0"))


def test_metrics_opening_drawn(w: World, db_session: Session) -> None:
    c = w.sale(contracted_qty_t=D("1000.000"), opening_drawn_qty_t=D("763.600"))
    m = metrics(db_session, c)
    assert (m.state, m.drawn_t, m.remaining_t) == (ContractState.OK, D("763.600"), D("236.400"))


def test_metrics_over(w: World, db_session: Session) -> None:
    c = w.sale(contracted_qty_t=D("100.000"), opening_drawn_qty_t=D("130.000"))
    m = metrics(db_session, c)
    assert (m.state, m.remaining_t) == (ContractState.OVER, D("-30.000"))


def test_metrics_for_many(w: World, db_session: Session) -> None:
    a = w.sale("2026-A", contracted_qty_t=D("10.000"))
    b = w.sale("2026-B")
    got = metrics_for(db_session, [a, b])
    assert got[a.id].state is ContractState.OK and got[b.id].state is ContractState.OPEN
