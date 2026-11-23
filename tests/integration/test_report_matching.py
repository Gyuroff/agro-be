"""Справки от доставчик: модели и съвпадение (agro-l2v.8)."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.audit_context import AuditContext
from app.domain.plates import normalize_plate
from app.models.contracts import Contract
from app.models.mail import InboundEmail
from app.models.masterdata import Counterparty, Product, Warehouse
from app.models.reports import MatchStatus, SupplierReport, SupplierReportLine
from app.models.trips import ClientRequest, Trip, TripStatus
from app.services.report_matching import match_lines, suggest_substitutes
from app.services.trips import PlannedTripData, TripService

D = Decimal


class World:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.client = Counterparty(name="Агро Трейд", legal_name="Агро Трейд ООД", eik="111111111")
        self.supplier = Counterparty(name="Агро Сует", legal_name="Агро Сует АД", eik="222222222")
        self.other_supplier = Counterparty(name="Друг", legal_name="Друг АД", eik="333333333")
        self.product = Product(name="Пелети")
        session.add_all([self.client, self.supplier, self.other_supplier, self.product])
        session.flush()
        self.wh = Warehouse(counterparty_id=self.supplier.id, name="Тервел")
        session.add(self.wh)
        session.flush()
        self.sale = self._contract("sale", "2026-018", self.client)
        self.purchase = self._contract("purchase", "П-2026-007", self.supplier, warehouse_id=self.wh.id)
        wh_other = Warehouse(counterparty_id=self.other_supplier.id, name="Друг склад")
        session.add(wh_other)
        session.flush()
        self.purchase_other = self._contract(
            "purchase", "П-2026-008", self.other_supplier, warehouse_id=wh_other.id
        )
        mail = InboundEmail(
            message_id="<rep@x>",
            from_address="sales@agrosuet.bg",
            received_at=clock.now(),
            raw_path="/tmp/x.eml",
            auth_ok=True,
        )
        session.add(mail)
        session.flush()
        self.mail = mail
        self.request = ClientRequest(inbound_email_id=mail.id, sale_contract_id=self.sale.id)
        session.add(self.request)
        session.flush()
        self.report = SupplierReport(inbound_email_id=mail.id, supplier_id=self.supplier.id)
        session.add(self.report)
        session.flush()

    def _contract(self, side: str, number: str, cp: Counterparty, **kw: object) -> Contract:
        c = Contract(
            number=number,
            side=side,
            counterparty_id=cp.id,
            product_id=self.product.id,
            valid_from=date(2026, 1, 1),
            **kw,
        )
        self.session.add(c)
        self.session.flush()
        return c

    def trip(self, plate: str, day: date, *, other_supplier: bool = False) -> Trip:
        return TripService(self.session).create_planned(
            PlannedTripData(
                request_id=self.request.id,
                sale_contract=self.sale,
                purchase_contract=self.purchase_other if other_supplier else self.purchase,
                planned_date=day,
                plate=plate,
                planned_qty_t=D("25.000"),
            )
        )

    def line(self, row_no: int, plate: str, day: date | None) -> SupplierReportLine:
        line = SupplierReportLine(
            report_id=self.report.id,
            row_no=row_no,
            load_date=day,
            plate_raw=plate,
            plate_norm=normalize_plate(plate),
            qty_t=D("24.000"),
        )
        self.session.add(line)
        self.session.flush()
        return line

    def lines(self) -> list[SupplierReportLine]:
        self.session.expire_all()
        return list(
            self.session.scalars(
                select(SupplierReportLine)
                .where(SupplierReportLine.report_id == self.report.id)
                .order_by(SupplierReportLine.id)
            )
        )


@pytest.fixture
def w(db_session: Session, audit_ctx: AuditContext) -> World:
    return World(db_session)


def counts(lines: list[SupplierReportLine]) -> tuple[int, int, int]:
    def n(status: MatchStatus) -> int:
        return sum(1 for line in lines if line.match_status == status.value)

    return n(MatchStatus.MATCHED), n(MatchStatus.UNPLANNED), n(MatchStatus.MISSING)


def test_figma_screen_3_counters(w: World, db_session: Session) -> None:
    day = date(2026, 11, 11)
    for i in range(11):
        plate = f"СА {1000 + i} ВХ"
        w.trip(plate, day)
        w.line(i + 1, plate, day)
    w.trip("СВ 7890 ТР", day)  # планиран, но няма в справката → missing
    w.line(12, "РВ 2211 ТТ", day)  # в справката, но не е планиран → unplanned
    result = match_lines(db_session, w.report)
    assert counts(result) == (11, 1, 1)
    assert counts(w.lines()) == (11, 1, 1)
    missing = next(line for line in w.lines() if line.match_status == "missing")
    assert (missing.row_no, missing.plate_norm, missing.warehouse_id) == (None, "CB7890TP", w.wh.id)
    assert missing.trip_id is not None


def test_match_is_idempotent(w: World, db_session: Session) -> None:
    w.trip("СА 1234 ВХ", date(2026, 11, 11))
    w.trip("СВ 7890 ТР", date(2026, 11, 11))
    w.line(1, "СА 1234 ВХ", date(2026, 11, 11))
    match_lines(db_session, w.report)
    match_lines(db_session, w.report)
    assert counts(w.lines()) == (1, 0, 1)


def test_date_tolerance(w: World, db_session: Session) -> None:
    w.trip("СА 1111 ВХ", date(2026, 11, 10))
    w.trip("СА 2222 ВХ", date(2026, 11, 10))
    w.line(1, "СА 1111 ВХ", date(2026, 11, 11))  # +1 → съвпада
    w.line(2, "СА 2222 ВХ", date(2026, 11, 12))  # +2 → не съвпада
    match_lines(db_session, w.report)
    by_row = {line.row_no: line for line in w.lines() if line.row_no}
    assert by_row[1].match_status == "matched"
    assert by_row[2].match_status == "unplanned"


def test_closest_date_wins(w: World, db_session: Session) -> None:
    far = w.trip("СА 1234 ВХ", date(2026, 11, 10))
    near = w.trip("СА 1234 ВХ", date(2026, 11, 11))
    w.line(1, "СА 1234 ВХ", date(2026, 11, 11))
    match_lines(db_session, w.report)
    line = next(line for line in w.lines() if line.row_no == 1)
    assert line.trip_id == near.id
    missing = next(line for line in w.lines() if line.match_status == "missing")
    assert missing.trip_id == far.id


def test_other_supplier_and_non_planned_are_ignored(w: World, db_session: Session) -> None:
    w.trip("СА 1234 ВХ", date(2026, 11, 11), other_supplier=True)
    done = w.trip("СА 5555 ВХ", date(2026, 11, 11))
    done.status = TripStatus.CANCELLED.value  # само за теста
    db_session.flush()
    w.line(1, "СА 1234 ВХ", date(2026, 11, 11))
    w.line(2, "СА 5555 ВХ", date(2026, 11, 11))
    match_lines(db_session, w.report)
    assert counts(w.lines()) == (0, 2, 0)


def test_suggest_substitutes_links_rv2211tt_to_sv7890tr(w: World, db_session: Session) -> None:
    day = date(2026, 11, 11)
    w.trip("СВ 7890 ТР", day)
    w.line(1, "РВ 2211 ТТ", day)
    match_lines(db_session, w.report)
    done = suggest_substitutes(db_session, w.report)
    rows = w.lines()
    unplanned = next(r for r in rows if r.match_status == "unplanned")
    missing = next(r for r in rows if r.match_status == "missing")
    assert [r.id for r in done] == [unplanned.id]
    assert unplanned.substitute_for_line_id == missing.id
    assert unplanned.warehouse_id == w.wh.id  # от курса на missing реда
    assert (unplanned.plate_norm, missing.plate_norm) == ("PB2211TT", "CB7890TP")


def test_suggest_substitutes_respects_date_and_resolution(w: World, db_session: Session) -> None:
    w.trip("СВ 7890 ТР", date(2026, 11, 11))
    w.line(1, "РВ 2211 ТТ", date(2026, 11, 14))
    match_lines(db_session, w.report)
    assert suggest_substitutes(db_session, w.report) == []
