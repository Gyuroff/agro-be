"""Справка: GET /reports/{id} и действията substitute_accept / other_client / confirm (agro-br9.7)."""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core import clock
from app.core.audit_context import AuditContext
from app.main import create_app
from app.models.audit import AuditLog
from app.models.cards import ActionCard, CardKind, CardStatus
from app.models.contracts import Contract, ContractPrice
from app.models.reports import ReportStatus
from app.models.trips import Trip, TripEvent, TripOrigin, TripStatus
from app.schemas.cards import SupplierReportPayload
from app.services.cards import CardService
from app.services.report_matching import match_lines, suggest_substitutes
from app.services.users import UserService
from tests.integration.test_auth import EMAIL, PASSWORD, bearer, login
from tests.integration.test_report_matching import World as BaseWorld

D = Decimal
DAY = date(2026, 11, 11)


class World(BaseWorld):
    def __init__(self, session: Session) -> None:
        super().__init__(session)
        self.sale.supply_contract_id = self.purchase.id
        for contract, price in ((self.sale, "200.00"), (self.purchase, "150.00")):
            session.add(
                ContractPrice(contract_id=contract.id, valid_from=date(2026, 1, 1), price_per_t=D(price))
            )
        session.flush()
        self.card: ActionCard | None = None

    def make_card(self) -> ActionCard:
        match_lines(self.session, self.report)
        suggest_substitutes(self.session, self.report)
        self.report.total_rows = 12
        self.report.total_qty_t = D("298.800")
        card = CardService.create(
            self.session,
            CardKind.SUPPLIER_REPORT,
            title="Справка от Агро Сует",
            payload=SupplierReportPayload(
                report_id=self.report.id,
                inbound_email_id=self.mail.id,
                received_at=clock.now(),
                total_rows=12,
                total_qty_t=D("298.800"),
                matched=0,
                unplanned=0,
                missing=0,
                preview=[],
                substitutes=[],
                customer_names=[],
            ),
            supplier_report_id=self.report.id,
            inbound_email_id=self.mail.id,
        )
        self.card = card
        return card


@pytest.fixture
def w(db_session: Session, audit_ctx: AuditContext) -> World:
    return World(db_session)


@pytest.fixture
def api(db_session: Session, audit_ctx: AuditContext) -> Iterator[TestClient]:
    UserService.create(
        db_session, email=EMAIL, full_name="Георги Гюров", short_name="Георги", password=PASSWORD
    )
    db_session.flush()
    app = create_app()

    def override_db() -> Iterator[Session]:
        db_session.commit()
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        yield client


@pytest.fixture
def auth(api: TestClient) -> dict[str, str]:
    return bearer(login(api).json()["access_token"])


def figma(w: World) -> int:
    """10 съвпадащи + СВ 7890 ТР липсва + РВ 2211 ТТ непланиран (предложено заместване); връща id на реда."""
    for i in range(10):
        plate = f"СА {1000 + i} ВХ"
        w.trip(plate, DAY)
        w.line(i + 1, plate, DAY)
    w.trip("СВ 7890 ТР", DAY)
    return w.line(11, "РВ 2211 ТТ", DAY).id


def act(api: TestClient, auth: dict[str, str], card: ActionCard, action: str, body: dict | None = None):
    return api.post(f"/api/v1/cards/{card.id}/actions/{action}", headers=auth, json=body)


def test_get_report_figma(api: TestClient, auth: dict[str, str], w: World) -> None:
    line_id = figma(w)
    w.make_card()
    data = api.get(f"/api/v1/reports/{w.report.id}", headers=auth).json()
    assert data["counts"] == {"matched": 10, "unplanned": 1, "missing": 1}
    assert data["confirm_label_count"] == 10
    assert data["total_qty_t"] == "298.800"
    assert data["customer_names"] == ["Агро Трейд"]
    [sub] = data["needs_decision"]
    assert (sub["unplanned_line_id"], sub["new_plate"], sub["old_plate"]) == (
        line_id,
        "РВ 2211 ТТ",
        "СВ 7890 ТР",
    )
    assert sub["warehouse_name"] == "Тервел"
    assert len(data["matched"]) == 10

    assert act(api, auth, w.card, "substitute_accept", {"unplanned_line_id": line_id}).status_code == 200  # type: ignore[arg-type]
    data = api.get(f"/api/v1/reports/{w.report.id}", headers=auth).json()
    assert data["needs_decision"] == [] and data["confirm_label_count"] == 11


def test_get_report_404(api: TestClient, auth: dict[str, str]) -> None:
    assert api.get("/api/v1/reports/999", headers=auth).status_code == 404


def test_confirm_figma_11_completed_with_substitution(
    api: TestClient, auth: dict[str, str], w: World, db_session: Session
) -> None:
    line_id = figma(w)
    card = w.make_card()
    assert act(api, auth, card, "substitute_accept", {"unplanned_line_id": line_id}).status_code == 200
    response = act(api, auth, card, "confirm")
    assert response.status_code == 200, response.text
    assert response.json()["card"] is None

    db_session.expire_all()
    completed = db_session.scalars(select(Trip).where(Trip.status == TripStatus.COMPLETED.value)).all()
    assert len(completed) == 11
    swapped = next(t for t in completed if t.original_plate_norm == "CB7890TP")
    assert swapped.plate_norm == "PB2211TT" and swapped.plate_display == "РВ 2211 ТТ"
    assert (swapped.sale_price_per_t, swapped.purchase_price_per_t) == (D("200.00"), D("150.00"))
    assert swapped.load_date == DAY and swapped.actual_qty_t == D("24.000")
    kinds = db_session.scalars(select(TripEvent.kind).where(TripEvent.trip_id == swapped.id)).all()
    assert "plate_changed" in kinds
    assert db_session.scalars(select(Trip).where(Trip.status == TripStatus.UNPLANNED.value)).all() == []
    db_session.refresh(w.report)
    assert w.report.status == ReportStatus.CONFIRMED.value and w.report.confirmed_at is not None
    db_session.refresh(card)
    assert (card.status, card.resolution) == (CardStatus.DONE.value, "confirmed")


def test_prices_at_load_date(api: TestClient, auth: dict[str, str], w: World, db_session: Session) -> None:
    db_session.add(
        ContractPrice(contract_id=w.sale.id, valid_from=date(2026, 11, 11), price_per_t=D("230.00"))
    )
    trip = w.trip("СА 1111 ВХ", date(2026, 11, 10))
    line = w.line(1, "СА 1111 ВХ", date(2026, 11, 11))  # ±1 ден; цената е към load_date
    card = w.make_card()
    assert act(api, auth, card, "confirm").status_code == 200
    db_session.refresh(trip)
    assert trip.load_date == line.load_date and trip.sale_price_per_t == D("230.00")


def test_unresolved_unplanned_becomes_unplanned_trip(
    api: TestClient, auth: dict[str, str], w: World, db_session: Session
) -> None:
    w.line(1, "ОВ 9999 ОВ", DAY)
    card = w.make_card()
    assert act(api, auth, card, "confirm").status_code == 200
    db_session.expire_all()
    trip = db_session.scalars(select(Trip).where(Trip.origin == TripOrigin.REPORT_UNPLANNED.value)).one()
    assert trip.status == TripStatus.UNPLANNED.value and trip.product_id == w.product.id
    listed = api.get("/api/v1/trips?from=2026-11-01&to=2026-11-30&status=unplanned", headers=auth).json()
    assert [i["id"] for g in listed["groups"] for i in g["items"]] == [trip.id]


def test_unplanned_ambiguous_product_is_422_and_card_stays_open(
    api: TestClient, auth: dict[str, str], w: World, db_session: Session
) -> None:
    from app.models.masterdata import Product

    other = Product(name="Брикети")
    db_session.add(other)
    db_session.flush()
    db_session.add(
        Contract(
            number="П-2026-099",
            side="purchase",
            counterparty_id=w.supplier.id,
            product_id=other.id,
            warehouse_id=w.wh.id,
            valid_from=date(2026, 1, 1),
        )
    )
    w.line(1, "ОВ 9999 ОВ", DAY)
    card = w.make_card()
    response = act(api, auth, card, "confirm")
    assert response.status_code == 422 and response.json()["error"]["code"] == "product_unknown"
    db_session.refresh(card)
    assert card.status == CardStatus.OPEN.value


def test_other_client(api: TestClient, auth: dict[str, str], w: World, db_session: Session) -> None:
    line = w.line(1, "ОВ 9999 ОВ", DAY)
    card = w.make_card()
    other_client = w.client  # същият клиент, втори продажен договор
    sale2 = Contract(
        number="2026-019",
        side="sale",
        counterparty_id=other_client.id,
        product_id=w.product.id,
        valid_from=date(2026, 1, 1),
        supply_contract_id=w.purchase.id,
    )
    db_session.add(sale2)
    db_session.flush()
    db_session.add(ContractPrice(contract_id=sale2.id, valid_from=date(2026, 1, 1), price_per_t=D("205.00")))
    bad = act(
        api, auth, card, "other_client", {"unplanned_line_id": line.id, "sale_contract_id": w.purchase.id}
    )
    assert bad.status_code == 400  # не е продажба
    ok = act(api, auth, card, "other_client", {"unplanned_line_id": line.id, "sale_contract_id": sale2.id})
    assert ok.status_code == 200
    assert act(api, auth, card, "confirm").status_code == 200
    db_session.expire_all()
    trip = db_session.scalars(select(Trip).where(Trip.origin == TripOrigin.REPORT_OTHER_CLIENT.value)).one()
    assert trip.status == TripStatus.COMPLETED.value and trip.sale_contract_id == sale2.id
    assert trip.sale_price_per_t == D("205.00")


def test_other_client_wrong_product_400(
    api: TestClient, auth: dict[str, str], w: World, db_session: Session
) -> None:
    from app.models.masterdata import Product

    line = w.line(1, "ОВ 9999 ОВ", DAY)
    card = w.make_card()
    other = Product(name="Брикети")
    db_session.add(other)
    db_session.flush()
    sale = Contract(
        number="2026-020",
        side="sale",
        counterparty_id=w.client.id,
        product_id=other.id,
        valid_from=date(2026, 1, 1),
        supply_contract_id=w.purchase.id,
    )
    db_session.add(sale)
    db_session.flush()
    response = act(
        api, auth, card, "other_client", {"unplanned_line_id": line.id, "sale_contract_id": sale.id}
    )
    assert response.status_code == 400


def test_double_confirm_is_409(api: TestClient, auth: dict[str, str], w: World) -> None:
    w.trip("СА 1111 ВХ", DAY)
    w.line(1, "СА 1111 ВХ", DAY)
    card = w.make_card()
    assert act(api, auth, card, "confirm").status_code == 200
    again = act(api, auth, card, "confirm")
    assert again.status_code == 409 and again.json()["error"]["code"] == "card_not_open"


def test_substitute_accept_requires_suggestion(api: TestClient, auth: dict[str, str], w: World) -> None:
    line = w.line(1, "ОВ 9999 ОВ", DAY)
    card = w.make_card()
    response = act(api, auth, card, "substitute_accept", {"unplanned_line_id": line.id})
    assert response.status_code == 400
    assert act(api, auth, card, "substitute_accept", {}).status_code == 400


def test_audit_actor_is_confirming_user(
    api: TestClient, auth: dict[str, str], w: World, db_session: Session
) -> None:
    line_id = figma(w)
    card = w.make_card()
    act(api, auth, card, "substitute_accept", {"unplanned_line_id": line_id})
    assert act(api, auth, card, "confirm").status_code == 200
    db_session.expire_all()
    trip_ids = list(db_session.scalars(select(Trip.id).where(Trip.status == TripStatus.COMPLETED.value)))
    assert len(trip_ids) == 11
    confirm_actor = db_session.get(ActionCard, card.id).resolved_by_id  # type: ignore[union-attr]
    assert confirm_actor is not None
    rows = db_session.execute(
        select(AuditLog.entity_id, AuditLog.actor_id, AuditLog.action).where(
            AuditLog.entity_type == "trips", AuditLog.entity_id.in_(trip_ids)
        )
    ).all()
    updates = [r for r in rows if r.action == "update"]
    assert {r.entity_id for r in updates} == set(trip_ids)
    assert {r.actor_id for r in updates} == {confirm_actor}
