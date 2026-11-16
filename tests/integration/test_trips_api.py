"""API за курсове: /trips, /trips/{id}, cancel, link-contract (agro-br9.2)."""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core import clock
from app.core.audit_context import AuditContext
from app.main import create_app
from app.models.trips import Trip
from app.services.trips import TripService
from app.services.users import UserService
from tests.integration.test_auth import EMAIL, PASSWORD, bearer, login
from tests.integration.test_trips import World

D = Decimal
TODAY = date(2026, 11, 12)


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
        db_session.commit()  # данните на теста да оцелеят rollback-а при грешка в заявката
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


def planned_on(w: World, day: date, qty: str = "25.200") -> Trip:
    trip = w.planned(qty)
    trip.planned_date = day
    w.session.flush()
    return trip


def test_list_groups_and_sums_figma_example(
    api: TestClient, auth: dict[str, str], w: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    for qty in ("12.500", "12.500", "12.490", "12.490"):
        trip = planned_on(w, TODAY, qty)
        TripService(w.session).complete(trip, actual_qty_t=D(qty), load_date=TODAY)
    for _ in range(3):
        planned_on(w, date(2026, 11, 13))
    monkeypatch.setattr(clock, "today_sofia", lambda: TODAY)
    response = api.get("/api/v1/trips", params={"from": "2026-11-09", "to": "2026-11-15"}, headers=auth)
    assert response.status_code == 200
    today, tomorrow = response.json()["groups"]
    assert (today["date"], today["count"], today["qty_t"], today["qty_kind"]) == (
        "2026-11-12",
        4,
        "49.980",
        "actual",
    )
    assert (tomorrow["date"], tomorrow["count"], tomorrow["qty_t"], tomorrow["qty_kind"]) == (
        "2026-11-13",
        3,
        "75.600",
        "planned",
    )
    assert today["items"][0]["actual_qty_t"] == "12.500"
    assert tomorrow["items"][0]["actual_qty_t"] is None


def test_list_filters(api: TestClient, auth: dict[str, str], w: World) -> None:
    done = planned_on(w, TODAY)
    TripService(w.session).complete(done, actual_qty_t=D("25.000"), load_date=TODAY)
    planned_on(w, TODAY)
    only = api.get("/api/v1/trips", params={"status": "planned"}, headers=auth).json()["groups"]
    by_contract = api.get("/api/v1/trips", params={"contract_id": w.sale.id}, headers=auth).json()
    other = api.get("/api/v1/trips", params={"contract_id": 999999}, headers=auth).json()
    bad = api.get("/api/v1/trips", params={"status": "nope"}, headers=auth)
    assert [g["count"] for g in only] == [1]
    assert by_contract["groups"][0]["count"] == 2
    assert other == {"groups": []}
    assert bad.status_code == 400


def test_detail_and_allowed_actions(api: TestClient, auth: dict[str, str], w: World) -> None:
    trip = w.planned()
    body = api.get(f"/api/v1/trips/{trip.id}", headers=auth).json()
    assert body["allowed_actions"] == ["cancel"]
    assert body["customer"]["name"] == "Агро Трейд"
    assert body["sale_contract"] == {"id": w.sale.id, "number": "2026-018"}
    assert body["supplier"]["name"] == "Агро Сует"
    assert body["warehouse"] == "Тервел"
    assert body["version"] == trip.version
    assert body["events"][0]["text"].startswith("Заявен")
    assert body["events"][0]["at"].endswith("Z")
    unplanned = w.unplanned()
    detail = api.get(f"/api/v1/trips/{unplanned.id}", headers=auth).json()
    assert detail["allowed_actions"] == ["link_contract"]
    assert detail["customer"] is None and detail["sale_contract"] is None
    assert api.get("/api/v1/trips/999999", headers=auth).status_code == 404


def test_cancel(api: TestClient, auth: dict[str, str], w: World) -> None:
    trip = w.planned()
    response = api.post(
        f"/api/v1/trips/{trip.id}/cancel",
        json={"version": trip.version, "reason": "по телефона"},
        headers=auth,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelled" and body["allowed_actions"] == []
    assert body["events"][-1]["text"] == "Отказан от потребител — по телефона"
    w.session.refresh(trip)
    assert (trip.cancel_source, trip.cancel_reason) == ("user", "по телефона")


def test_cancel_completed_is_409_invalid_transition(api: TestClient, auth: dict[str, str], w: World) -> None:
    trip = w.planned()
    TripService(w.session).complete(trip, actual_qty_t=D("25.000"), load_date=date(2026, 3, 5))
    response = api.post(f"/api/v1/trips/{trip.id}/cancel", json={"version": trip.version}, headers=auth)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_transition"


def test_version_conflict_is_409(api: TestClient, auth: dict[str, str], w: World) -> None:
    trip = w.planned()
    response = api.post(f"/api/v1/trips/{trip.id}/cancel", json={"version": trip.version + 5}, headers=auth)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "version_conflict"
    w.session.refresh(trip)
    assert trip.status == "planned"
    unplanned = w.unplanned()
    response = api.post(
        f"/api/v1/trips/{unplanned.id}/link-contract",
        json={"sale_contract_id": w.sale.id, "version": unplanned.version + 1},
        headers=auth,
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == "version_conflict"


def test_link_contract(api: TestClient, auth: dict[str, str], w: World) -> None:
    w.sale.supply_contract_id = w.purchase.id
    w.session.flush()
    trip = w.unplanned()
    response = api.post(
        f"/api/v1/trips/{trip.id}/link-contract",
        json={"sale_contract_id": w.sale.id, "version": trip.version},
        headers=auth,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed" and body["allowed_actions"] == []
    assert body["sale_contract"]["id"] == w.sale.id and body["customer"]["id"] == w.client.id
    assert next(e["text"] for e in body["events"]).startswith("Свързан с договор 2026-018")
    w.session.refresh(trip)
    assert (trip.purchase_contract_id, trip.sale_price_per_t, trip.purchase_price_per_t) == (
        w.purchase.id,
        D("210.00"),
        D("150.00"),
    )


def test_link_contract_without_price_is_422(api: TestClient, auth: dict[str, str], w: World) -> None:
    w.sale.supply_contract_id = w.purchase.id
    w.session.flush()
    trip = w.unplanned()
    trip.load_date = date(2025, 12, 31)  # преди първата цена (2026-01-01)
    w.session.flush()
    response = api.post(
        f"/api/v1/trips/{trip.id}/link-contract",
        json={"sale_contract_id": w.sale.id, "version": trip.version},
        headers=auth,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_price_for_date"
    w.session.refresh(trip)
    assert trip.status == "unplanned"


def test_link_contract_validations(api: TestClient, auth: dict[str, str], w: World) -> None:
    trip = w.unplanned()
    url = f"/api/v1/trips/{trip.id}/link-contract"
    no_supply = api.post(url, json={"sale_contract_id": w.sale.id, "version": trip.version}, headers=auth)
    assert no_supply.status_code == 400
    wrong_side = api.post(
        url, json={"sale_contract_id": w.purchase.id, "version": trip.version}, headers=auth
    )
    assert wrong_side.status_code == 400
    missing = api.post(url, json={"sale_contract_id": 999999, "version": trip.version}, headers=auth)
    assert missing.status_code == 404
    planned = w.planned()
    not_unplanned = api.post(
        f"/api/v1/trips/{planned.id}/link-contract",
        json={"sale_contract_id": w.sale.id, "version": planned.version},
        headers=auth,
    )
    assert not_unplanned.status_code in (400, 409)


def test_trips_require_login(api: TestClient) -> None:
    assert api.get("/api/v1/trips").status_code == 401
    assert api.post("/api/v1/trips/1/cancel", json={"version": 1}).status_code == 401
