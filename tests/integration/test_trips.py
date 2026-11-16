"""trips, преходи, събития, показатели на договор (agro-l2v.7)."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import clock
from app.core.audit_context import AuditContext
from app.core.errors import DomainError
from app.domain.pricing import NoPriceForDate
from app.models.contracts import Contract, ContractPrice
from app.models.mail import InboundEmail, OutboundEmail
from app.models.masterdata import Counterparty, Product, Warehouse
from app.models.trips import (
    CancelSource,
    ClientRequest,
    ClientRequestLine,
    Trip,
    TripEvent,
    TripOrigin,
)
from app.services.contract_metrics import ContractState, metrics
from app.services.trips import PlannedTripData, TripService

D = Decimal


class World:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.client = Counterparty(name="Агро Трейд", legal_name="Агро Трейд ООД", eik="111111111")
        self.supplier = Counterparty(name="Агро Сует", legal_name="Агро Сует АД", eik="222222222")
        self.pellets = Product(name="Пелети")
        session.add_all([self.client, self.supplier, self.pellets])
        session.flush()
        self.wh = Warehouse(counterparty_id=self.supplier.id, name="Тервел")
        session.add(self.wh)
        session.flush()
        self.sale = self._contract("sale", "2026-018", self.client)
        self.purchase = self._contract("purchase", "П-2026-007", self.supplier, warehouse_id=self.wh.id)
        for contract, price in ((self.sale, "200.00"), (self.purchase, "150.00")):
            session.add(
                ContractPrice(contract_id=contract.id, valid_from=date(2026, 1, 1), price_per_t=D(price))
            )
        session.add(
            ContractPrice(contract_id=self.sale.id, valid_from=date(2026, 3, 1), price_per_t=D("210.00"))
        )
        session.flush()

    def _contract(self, side: str, number: str, cp: Counterparty, **kw: object) -> Contract:
        c = Contract(
            number=number,
            side=side,
            counterparty_id=cp.id,
            product_id=self.pellets.id,
            valid_from=date(2026, 1, 1),
            **kw,
        )
        self.session.add(c)
        self.session.flush()
        return c

    def request(self) -> ClientRequest:
        mail = InboundEmail(
            message_id=f"<req-{uuid.uuid4().hex}@x>",
            from_address="maria@agrotrade.bg",
            received_at=clock.now(),
            raw_path="/tmp/x.eml",
            auth_ok=True,
        )
        self.session.add(mail)
        self.session.flush()
        r = ClientRequest(inbound_email_id=mail.id, sale_contract_id=self.sale.id)
        self.session.add(r)
        self.session.flush()
        return r

    def planned(self, qty: str = "25.200", plate: str = "СА 1234 ВХ", **kw: object) -> Trip:
        data = PlannedTripData(
            request_id=self.request().id,
            sale_contract=self.sale,
            purchase_contract=self.purchase,
            planned_date=date(2026, 2, 10),
            plate=plate,
            planned_qty_t=D(qty),
            **kw,  # type: ignore[arg-type]
        )
        return TripService(self.session).create_planned(data)

    def unplanned(self) -> Trip:
        return TripService(self.session).create_unplanned(
            origin=TripOrigin.REPORT_UNPLANNED,
            supplier_id=self.supplier.id,
            product_id=self.pellets.id,
            plate="B 5555 KT",
            planned_date=date(2026, 2, 12),
            actual_qty_t=D("24.860"),
            load_date=date(2026, 3, 5),
        )


@pytest.fixture
def w(db_session: Session, audit_ctx: AuditContext) -> World:
    return World(db_session)


def events(session: Session, trip: Trip) -> list[tuple[str, str]]:
    rows = session.scalars(select(TripEvent).where(TripEvent.trip_id == trip.id).order_by(TripEvent.id))
    return [(e.kind, e.text) for e in rows]


def assert_invalid(trip: Trip, action: object) -> None:
    before = trip.status
    with pytest.raises(DomainError) as exc:
        action()  # type: ignore[operator]
    assert (exc.value.code, exc.value.http_status) == ("invalid_transition", 409)
    assert trip.status == before


def test_create_planned(w: World, db_session: Session) -> None:
    req = w.request()
    out = OutboundEmail(
        reference_code="АИТ-0001",
        purpose="supplier_order",
        to_addresses=["a@b.bg"],
        subject="s",
        body_text="t",
    )
    db_session.add(out)
    db_session.flush()
    trip = w.planned(outbound_email_id=out.id, inbound_email_id=req.inbound_email_id)
    assert (trip.status, trip.origin) == ("planned", "request")
    assert (trip.plate_norm, trip.plate_display) == ("CA1234BX", "СА 1234 ВХ")
    assert (trip.customer_id, trip.supplier_id, trip.warehouse_id) == (w.client.id, w.supplier.id, w.wh.id)
    assert events(db_session, trip) == [
        ("requested", "Заявен — имейл от Агро Трейд"),
        ("sent_to_supplier", "Изпратен към Агро Сует"),
    ]


def test_complete_snapshots_prices_at_load_date(w: World, db_session: Session) -> None:
    trip = w.planned()
    TripService(db_session).complete(trip, actual_qty_t=D("24.860"), load_date=date(2026, 3, 5))
    assert trip.status == "completed"
    assert (trip.sale_price_per_t, trip.purchase_price_per_t) == (D("210.00"), D("150.00"))
    assert trip.completed_at is not None
    assert events(db_session, trip)[-1] == ("completed", "Изпълнен — 24,86 т, натоварен 05.03.2026")
    # по-ранна дата → старата цена
    other = w.planned()
    TripService(db_session).complete(other, actual_qty_t=D("25"), load_date=date(2026, 2, 27))
    assert other.sale_price_per_t == D("200.00")


def test_complete_without_price_is_422_and_keeps_planned(w: World, db_session: Session) -> None:
    trip = w.planned()
    with pytest.raises(NoPriceForDate):
        TripService(db_session).complete(trip, actual_qty_t=D("25"), load_date=date(2025, 12, 31))
    assert trip.status == "planned"


def test_no_price_changes_nothing(w: World, db_session: Session) -> None:
    svc = TripService(db_session)
    trip = w.planned()
    with pytest.raises(NoPriceForDate):
        svc.complete(trip, actual_qty_t=D("25"), load_date=date(2025, 12, 31), plate="B 5555 KT")
    assert (trip.plate_norm, trip.original_plate_norm, trip.actual_qty_t) == ("CA1234BX", None, None)
    assert [k for k, _ in events(db_session, trip)] == ["requested"]

    late = w.unplanned()
    late.load_date = date(2025, 12, 31)
    with pytest.raises(NoPriceForDate):
        svc.link_unplanned(late, sale_contract=w.sale, purchase_contract=w.purchase)
    assert (late.status, late.sale_contract_id, late.customer_id) == ("unplanned", None, None)


def test_complete_with_plate_change(w: World, db_session: Session) -> None:
    trip = w.planned()
    TripService(db_session).complete(
        trip, actual_qty_t=D("25"), load_date=date(2026, 3, 1), plate="B 5555 KT"
    )
    assert (trip.original_plate_norm, trip.plate_norm, trip.plate_display) == (
        "CA1234BX",
        "B5555KT",
        "В 5555 КТ",
    )
    kinds = [k for k, _ in events(db_session, trip)]
    assert kinds == ["requested", "plate_changed", "completed"]


def test_cancel(w: World, db_session: Session) -> None:
    trip = w.planned()
    TripService(db_session).cancel(trip, source=CancelSource.EMAIL, reason="повреда")
    assert (trip.status, trip.cancel_source, trip.cancel_reason) == ("cancelled", "email", "повреда")
    assert trip.cancelled_at is not None
    assert events(db_session, trip)[-1] == ("cancelled", "Отказан по имейл от доставчика — повреда")


def test_unplanned_then_link(w: World, db_session: Session) -> None:
    trip = w.unplanned()
    assert (trip.status, trip.sale_contract_id) == ("unplanned", None)
    TripService(db_session).link_unplanned(trip, sale_contract=w.sale, purchase_contract=w.purchase)
    assert trip.status == "completed"
    assert (trip.customer_id, trip.warehouse_id) == (w.client.id, w.wh.id)
    assert (trip.sale_price_per_t, trip.purchase_price_per_t) == (D("210.00"), D("150.00"))
    assert [k for k, _ in events(db_session, trip)] == ["linked", "completed"]


def test_create_unplanned_rejects_request_origin(w: World, db_session: Session) -> None:
    with pytest.raises(DomainError):
        TripService(db_session).create_unplanned(
            origin=TripOrigin.REQUEST,
            supplier_id=w.supplier.id,
            product_id=w.pellets.id,
            plate="CA1234BX",
            planned_date=date(2026, 2, 1),
            actual_qty_t=D("1"),
            load_date=date(2026, 2, 1),
        )


def test_forbidden_transitions(w: World, db_session: Session) -> None:
    svc = TripService(db_session)
    done = w.planned()
    svc.complete(done, actual_qty_t=D("25"), load_date=date(2026, 3, 1))
    assert_invalid(done, lambda: svc.cancel(done, source=CancelSource.USER))  # completed → cancelled
    assert_invalid(done, lambda: svc.complete(done, actual_qty_t=D("1"), load_date=date(2026, 3, 1)))
    assert_invalid(done, lambda: svc.link_unplanned(done, sale_contract=w.sale, purchase_contract=w.purchase))

    cancelled = w.planned()
    svc.cancel(cancelled, source=CancelSource.USER)
    assert_invalid(cancelled, lambda: svc.cancel(cancelled, source=CancelSource.USER))
    assert_invalid(
        cancelled, lambda: svc.complete(cancelled, actual_qty_t=D("1"), load_date=date(2026, 3, 1))
    )

    planned = w.planned()
    assert_invalid(
        planned, lambda: svc.link_unplanned(planned, sale_contract=w.sale, purchase_contract=w.purchase)
    )

    unplanned = w.unplanned()
    assert_invalid(unplanned, lambda: svc.cancel(unplanned, source=CancelSource.USER))
    assert_invalid(
        unplanned, lambda: svc.complete(unplanned, actual_qty_t=D("1"), load_date=date(2026, 3, 1))
    )


def test_check_constraints(w: World, db_session: Session) -> None:
    trip = w.planned()
    with pytest.raises(IntegrityError), db_session.begin_nested():
        trip.status = "completed"  # без actual_qty_t / load_date
        db_session.flush()
    db_session.expire_all()
    with pytest.raises(IntegrityError), db_session.begin_nested():
        trip.sale_contract_id = None  # статус planned изисква договор
        db_session.flush()
    db_session.expire_all()
    with pytest.raises(IntegrityError), db_session.begin_nested():
        trip.cancel_source = "phone"
        db_session.flush()


def test_request_lines(w: World, db_session: Session) -> None:
    req = w.request()
    line = ClientRequestLine(
        request_id=req.id,
        line_no=1,
        planned_date=date(2026, 2, 10),
        plate_raw="са 1234 вх",
        plate_norm="CA1234BX",
        planned_qty_t=D("25.200"),
    )
    db_session.add(line)
    db_session.flush()
    db_session.refresh(line)
    db_session.refresh(req)
    assert (line.uncertain_fields, line.is_removed, req.status) == ([], False, "draft")
    db_session.add(
        ClientRequestLine(
            request_id=req.id,
            line_no=1,
            planned_date=date(2026, 2, 10),
            plate_raw="x",
            plate_norm="X",
            planned_qty_t=D("1"),
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_metrics_figma_example(w: World, db_session: Session) -> None:
    """Договорено 1000, изтеглено 763.6, заявено 302.4 → remaining −66.0, state over."""
    w.sale.contracted_qty_t = D("1000")
    w.sale.opening_drawn_qty_t = D("100.000")
    db_session.flush()
    svc = TripService(db_session)
    done = [w.planned(qty="25.200") for _ in range(4)]
    for t, qty in zip(done, ("200.000", "200.000", "200.000", "63.600"), strict=True):
        svc.complete(t, actual_qty_t=D(qty), load_date=date(2026, 3, 5))
    for _ in range(12):
        w.planned(qty="25.200")  # 12 × 25.2 = 302.4 заявено
    svc.cancel(w.planned(qty="500"), source=CancelSource.USER)  # отказаният не се брои
    w.unplanned()  # непланираният не се брои
    m = metrics(db_session, w.sale)
    assert (m.drawn_t, m.requested_t, m.remaining_t, m.state) == (
        D("763.600"),
        D("302.400"),
        D("-66.000"),
        ContractState.OVER,
    )
    # покупният договор се смята по същите курсове
    assert metrics(db_session, w.purchase).state is ContractState.OPEN
    assert metrics(db_session, w.purchase).requested_t == D("302.400")
