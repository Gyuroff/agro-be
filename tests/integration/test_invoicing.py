"""InvoiceDraftBuilder и invoicing.daily_billing (agro-66f.1)."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from freezegun import freeze_time
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.audit_context import AuditContext
from app.models.cards import ActionCard, CardKind
from app.models.contracts import Contract, ContractPrice
from app.models.invoicing import Invoice, InvoiceLine, InvoiceTrip
from app.models.mail import InboundEmail
from app.models.masterdata import Counterparty, Product, Warehouse
from app.models.trips import ClientRequest, Trip
from app.services.invoicing import InvoiceDraftBuilder, bill_per_trip, run_daily_billing
from app.services.trips import PlannedTripData, TripService

D = Decimal
MONDAY = date(2026, 11, 16)  # понеделник


class World:
    def __init__(self, session: Session, *, mode: str = "shadow") -> None:
        self.session = session
        self.client = Counterparty(
            name="Агро Трейд",
            legal_name="Агро Трейд ООД",
            eik="111111111",
            vat_number="BG111111111",
            address_city="Варна",
            address_line="ул. Морска 1",
            mol="Иван Иванов",
            invoice_email="invoices@agrotrade.bg",
            default_billing_mode="per_period",
            default_billing_rrule="FREQ=WEEKLY;BYDAY=MO",
            default_payment_terms_days=7,
            mega_code="M1",
            invoicing_mode=mode,
        )
        self.supplier = Counterparty(name="Пелет АД", legal_name="Пелет АД", eik="222222222")
        self.pellets = Product(name="Пелети")
        session.add_all([self.client, self.supplier, self.pellets])
        session.flush()
        self.wh = Warehouse(counterparty_id=self.supplier.id, name="Тервел")
        session.add(self.wh)
        session.flush()
        self.sale = self._contract("sale", "2026-018", self.client)
        self.purchase = self._contract("purchase", "П-2026-007", self.supplier, warehouse_id=self.wh.id)
        session.add_all(
            [
                ContractPrice(contract_id=self.sale.id, valid_from=date(2026, 1, 1), price_per_t=D("120.00")),
                ContractPrice(
                    contract_id=self.purchase.id, valid_from=date(2026, 1, 1), price_per_t=D("100.00")
                ),
            ]
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

    def trip(self, qty: str, load: date, *, complete: bool = True, plate: str = "СА 1234 ВХ") -> Trip:
        mail = InboundEmail(
            message_id=f"<r-{uuid.uuid4().hex}@x>",
            from_address="m@agrotrade.bg",
            received_at=clock.now(),
            raw_path="/tmp/x.eml",
            auth_ok=True,
        )
        self.session.add(mail)
        self.session.flush()
        req = ClientRequest(inbound_email_id=mail.id, sale_contract_id=self.sale.id)
        self.session.add(req)
        self.session.flush()
        svc = TripService(self.session)
        t = svc.create_planned(
            PlannedTripData(
                request_id=req.id,
                sale_contract=self.sale,
                purchase_contract=self.purchase,
                planned_date=load,
                plate=plate,
                planned_qty_t=D(qty),
            )
        )
        if complete:
            svc.complete(t, actual_qty_t=D(qty), load_date=load)
        return t

    def figma_week(self) -> list[Trip]:
        """11 курса, 273,760 т (десет по 24,870 и един 25,060)."""
        trips = [self.trip("24.870", date(2026, 11, 9 + i % 6), plate=f"СА {1000 + i} ВХ") for i in range(10)]
        trips.append(self.trip("25.060", date(2026, 11, 15), plate="СА 2000 ВХ"))
        return trips


@pytest.fixture
def w(db_session: Session, audit_ctx: AuditContext) -> World:
    return World(db_session)


def lines_of(session: Session, inv: Invoice) -> list[InvoiceLine]:
    return list(
        session.scalars(
            select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id).order_by(InvoiceLine.line_no)
        )
    )


def cards(session: Session) -> list[ActionCard]:
    return list(session.scalars(select(ActionCard).where(ActionCard.kind == CardKind.INVOICE_DUE.value)))


def test_figma_numbers(w: World, db_session: Session) -> None:
    w.figma_week()
    inv = InvoiceDraftBuilder(db_session).build(w.client, MONDAY, w.sale)
    assert inv is not None
    (line,) = lines_of(db_session, inv)
    assert (line.qty_t, line.unit_price, line.amount) == (D("273.760"), D("120.00"), D("32851.20"))
    assert (inv.subtotal, inv.vat_amount, inv.total) == (D("32851.20"), D("6570.24"), D("39421.44"))
    n = db_session.scalars(select(InvoiceTrip).where(InvoiceTrip.invoice_id == inv.id)).all()
    assert len(n) == 11
    assert (inv.status, inv.mode, inv.billing_date) == ("draft", "shadow", MONDAY)
    assert (inv.period_from, inv.period_to) == (date(2026, 11, 9), date(2026, 11, 15))
    assert inv.due_date == date(2026, 11, 23)
    assert inv.recipient_name == "Агро Трейд ООД"
    assert inv.recipient_address == "Варна, ул. Морска 1"
    assert inv.supplier_legal_name  # снимка на нас (сийдната фирма)


def test_price_change_makes_two_lines(w: World, db_session: Session) -> None:
    db_session.add(
        ContractPrice(contract_id=w.sale.id, valid_from=date(2026, 11, 12), price_per_t=D("118.00"))
    )
    db_session.flush()
    w.trip("25.000", date(2026, 11, 10))
    w.trip("20.000", date(2026, 11, 13), plate="СА 2222 ВХ")
    inv = InvoiceDraftBuilder(db_session).build(w.client, MONDAY, w.sale)
    assert inv is not None
    rows = lines_of(db_session, inv)
    assert [(r.unit_price, r.qty_t, r.amount) for r in rows] == [
        (D("118.00"), D("20.000"), D("2360.00")),
        (D("120.00"), D("25.000"), D("3000.00")),
    ]
    assert inv.subtotal == D("5360.00")


def test_cancelled_planned_and_invoiced_trips_excluded(w: World, db_session: Session) -> None:
    ok = w.trip("25.000", date(2026, 11, 10))
    w.trip("25.000", date(2026, 11, 11), complete=False, plate="СА 3333 ВХ")  # planned
    cancelled = w.trip("25.000", date(2026, 11, 12), complete=False, plate="СА 4444 ВХ")
    from app.models.trips import CancelSource

    TripService(db_session).cancel(cancelled, source=CancelSource.USER)
    done = w.trip("25.000", date(2026, 11, 9), plate="СА 5555 ВХ")
    old = Invoice(
        counterparty_id=w.client.id,
        mode="erp",
        status="issued",
        billing_date=date(2026, 11, 9),
        period_from=date(2026, 11, 2),
        period_to=date(2026, 11, 8),
        subtotal=D("1"),
        vat_amount=D("0.20"),
        total=D("1.20"),
    )
    db_session.add(old)
    db_session.flush()
    done.invoice_id = old.id
    db_session.flush()
    inv = InvoiceDraftBuilder(db_session).build(w.client, MONDAY, w.sale)
    assert inv is not None
    linked = {
        t.trip_id for t in db_session.scalars(select(InvoiceTrip).where(InvoiceTrip.invoice_id == inv.id))
    }
    assert linked == {ok.id}


def test_late_report_goes_to_next_cycle(w: World, db_session: Session) -> None:
    w.trip("25.000", date(2026, 11, 10))
    late = w.trip("10.000", date(2026, 11, 12), complete=False, plate="СА 6666 ВХ")
    first = InvoiceDraftBuilder(db_session).build(w.client, MONDAY, w.sale)
    assert first is not None
    assert lines_of(db_session, first)[0].qty_t == D("25.000")
    TripService(db_session).complete(late, actual_qty_t=D("10.000"), load_date=date(2026, 11, 12))
    nxt = InvoiceDraftBuilder(db_session).build(w.client, date(2026, 11, 23), w.sale)
    assert nxt is not None and nxt.id != first.id
    # курсът от първата чернова не се брои втори път; закъснелият влиза в следващия цикъл
    assert [r.qty_t for r in lines_of(db_session, nxt)] == [D("10.000")]
    assert nxt.period_from == date(2026, 11, 12)  # по-стар от предишната дата → периодът се разширява


def test_rebuild_is_idempotent_and_recalculates(w: World, db_session: Session) -> None:
    w.trip("25.000", date(2026, 11, 10))
    b = InvoiceDraftBuilder(db_session)
    first = b.build(w.client, MONDAY, w.sale)
    assert first is not None
    w.trip("5.000", date(2026, 11, 11), plate="СА 7777 ВХ")
    again = b.build(w.client, MONDAY, w.sale)
    assert again is not None and again.id == first.id
    assert db_session.scalars(select(Invoice)).all() == [first]
    (line,) = lines_of(db_session, again)
    assert (line.line_no, line.qty_t) == (1, D("30.000"))
    assert again.total == D("3600.00") + D("720.00")


def test_issued_invoice_is_returned_untouched(w: World, db_session: Session) -> None:
    w.trip("25.000", date(2026, 11, 10))
    inv = InvoiceDraftBuilder(db_session).build(w.client, MONDAY, w.sale)
    assert inv is not None
    inv.status = "issued"
    db_session.flush()
    w.trip("5.000", date(2026, 11, 11), plate="СА 7777 ВХ")
    assert InvoiceDraftBuilder(db_session).build(w.client, MONDAY, w.sale) is inv
    assert inv.subtotal == D("3000.00")


def test_no_trips_gives_none(w: World, db_session: Session) -> None:
    assert InvoiceDraftBuilder(db_session).build(w.client, MONDAY, w.sale) is None
    assert db_session.scalars(select(Invoice)).all() == []


def test_merge_contracts_one_invoice(w: World, db_session: Session) -> None:
    w.client.merge_contracts_in_invoice = True
    other = w._contract("sale", "2026-019", w.client)
    db_session.add(ContractPrice(contract_id=other.id, valid_from=date(2026, 1, 1), price_per_t=D("130.00")))
    db_session.flush()
    w.trip("10.000", date(2026, 11, 10))
    t2 = w.trip("10.000", date(2026, 11, 11), plate="СА 8888 ВХ")
    t2.sale_contract_id = other.id
    t2.sale_price_per_t = D("130.00")
    db_session.flush()
    inv = InvoiceDraftBuilder(db_session).build(w.client, MONDAY, w.sale)
    assert inv is not None and inv.sale_contract_id is None
    assert {r.sale_contract_id for r in lines_of(db_session, inv)} == {w.sale.id, other.id}
    assert inv.subtotal == D("2500.00")


def test_separate_invoice_per_contract(w: World, db_session: Session) -> None:
    other = w._contract("sale", "2026-019", w.client)
    w.trip("10.000", date(2026, 11, 10))
    t2 = w.trip("10.000", date(2026, 11, 11), plate="СА 8888 ВХ")
    t2.sale_contract_id = other.id
    db_session.flush()
    b = InvoiceDraftBuilder(db_session)
    a, c = b.build(w.client, MONDAY, w.sale), b.build(w.client, MONDAY, other)
    assert a is not None and c is not None and a.id != c.id
    assert (a.sale_contract_id, c.sale_contract_id) == (w.sale.id, other.id)


def test_shadow_no_card(w: World, db_session: Session) -> None:
    w.figma_week()
    with freeze_time("2026-11-16 05:30:00"):  # 07:30 в София
        ids = run_daily_billing(db_session, clock.today_sofia())
    assert len(ids) == 1
    assert cards(db_session) == []


def test_erp_one_card(db_session: Session, audit_ctx: AuditContext) -> None:
    w = World(db_session, mode="erp")
    w.figma_week()
    with freeze_time("2026-11-16 05:30:00"):
        ids = run_daily_billing(db_session, clock.today_sofia())
        again = run_daily_billing(db_session, clock.today_sofia())
    assert ids == again and len(ids) == 1
    (card,) = cards(db_session)
    assert card.invoice_id == ids[0] and card.counterparty_id == w.client.id
    p = card.payload
    assert (p["trip_count"], p["total"], p["vat_amount"], p["vat_rate"]) == (
        11,
        "39421.44",
        "6570.24",
        "20.00",
    )
    assert p["schedule_label"] == "всеки понеделник"
    assert p["lines"] == [
        {"product": "Пелети", "qty_t": "273.760", "unit_price": "120.00", "amount": "32851.20"}
    ]


def test_daily_billing_skips_non_billing_day(db_session: Session, audit_ctx: AuditContext) -> None:
    w = World(db_session, mode="erp")
    w.figma_week()
    assert run_daily_billing(db_session, date(2026, 11, 17)) == []
    assert db_session.scalars(select(Invoice)).all() == []


def test_per_trip_builds_draft_for_today(db_session: Session, audit_ctx: AuditContext) -> None:
    w = World(db_session, mode="erp")
    w.client.default_billing_mode = "per_trip"
    w.client.default_billing_rrule = None
    t = w.trip("25.000", date(2026, 11, 16))
    with freeze_time("2026-11-16 12:00:00"):
        ids = bill_per_trip(db_session, [t])
    assert len(ids) == 1
    inv = db_session.get_one(Invoice, ids[0])
    assert (inv.billing_date, inv.period_from, inv.period_to) == (MONDAY, MONDAY, MONDAY)
    assert len(cards(db_session)) == 1
