"""API за договори и карта contract_over (agro-br9.3)."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import clock
from app.models.cards import ActionCard, CardKind, CardStatus
from app.models.contracts import ContractPrice
from app.models.trips import Trip
from app.services.contract_alerts import check_over
from tests.integration.test_trips import World
from tests.integration.test_trips_api import api, auth, w

__all__ = ["api", "auth", "w"]

D = Decimal
TODAY = date(2026, 11, 12)


@pytest.fixture(autouse=True)
def _today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock, "today_sofia", lambda: TODAY)


def over_card(w: World) -> ActionCard | None:
    return w.session.scalar(select(ActionCard).where(ActionCard.dedupe_key == f"contract_over:{w.sale.id}"))


def make_over(w: World) -> None:
    """Екран 10: 1000 т, изтеглени 763.6, заявени 302.4 → не стигат 66 т."""
    w.sale.contracted_qty_t = D("1000.000")
    w.sale.opening_drawn_qty_t = D("763.600")
    w.sale.valid_to = date(2026, 11, 30)
    w.session.flush()
    for _ in range(12):
        w.planned("25.200")


def test_list_matches_screen_numbers(api: TestClient, auth: dict[str, str], w: World) -> None:
    make_over(w)
    r = api.get("/api/v1/contracts", params={"side": "sale"}, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert (body["active_total"], body["sale_count"], body["purchase_count"]) == (2, 1, 1)
    (row,) = body["items"]
    assert row["number"] == "2026-018"
    assert row["counterparty_name"] == "Агро Трейд"
    assert (row["contracted_t"], row["drawn_t"], row["requested_t"]) == ("1000.000", "763.600", "302.400")
    assert (row["remaining_t"], row["state"]) == ("-66.000", "over")
    assert row["funds_balance"] is None


def test_list_excludes_expired_and_closed(api: TestClient, auth: dict[str, str], w: World) -> None:
    w.purchase.valid_to = date(2026, 11, 1)
    w.session.flush()
    body = api.get("/api/v1/contracts", headers=auth).json()
    assert body["active_total"] == 1
    assert [i["number"] for i in body["items"]] == ["2026-018"]


def test_list_open_contract_and_funds(api: TestClient, auth: dict[str, str], w: World) -> None:
    w.purchase.funds_balance = D("42000.00")
    w.purchase.funds_held_by = "counterparty"
    w.session.flush()
    row = api.get("/api/v1/contracts", params={"side": "purchase"}, headers=auth).json()["items"][0]
    assert row["state"] == "open"
    assert row["remaining_t"] is None
    assert row["warehouse_name"] == "Тервел"
    assert (row["funds_balance"], row["funds_held_by"]) == ("42000.00", "counterparty")
    assert row["funds_label"] == "Наш аванс при доставчика"


def test_detail_sale(api: TestClient, auth: dict[str, str], w: World) -> None:
    w.sale.billing_mode = "per_period"
    w.sale.billing_rrule = "FREQ=WEEKLY;BYDAY=MO"
    w.sale.payment_terms_days = 15
    w.session.flush()
    for _ in range(5):
        w.planned()
    r = api.get(f"/api/v1/contracts/{w.sale.id}", headers=auth)
    assert r.status_code == 200
    d = r.json()
    assert d["side"] == "sale"
    assert d["sale_price_per_t"] == "210.00"  # цената към днес: редът от 01.03
    assert d["purchase_prices"] == []
    assert d["billing_label"] == "всеки понеделник"
    assert d["next_billing_date"] == "2026-11-16"
    assert d["payment_terms_days"] == 15
    assert d["purchase_payment_mode"] is None
    assert len(d["recent_trips"]) == 4
    assert d["trips_summary"] == {"count": 0, "qty_t": "0.000"}


def test_detail_purchase_prices_by_packaging(api: TestClient, auth: dict[str, str], w: World) -> None:
    w.session.add_all(
        [
            ContractPrice(
                contract_id=w.purchase.id,
                valid_from=date(2026, 1, 1),
                packaging="bagged",
                price_per_t=D("158.00"),
            ),
            ContractPrice(
                contract_id=w.purchase.id,
                valid_from=date(2027, 1, 1),
                packaging="bulk",
                price_per_t=D("999.00"),
            ),
        ]
    )
    w.purchase.purchase_payment_mode = "prepaid"
    w.session.flush()
    d = api.get(f"/api/v1/contracts/{w.purchase.id}", headers=auth).json()
    assert d["purchase_prices"] == [
        {"packaging": "bulk", "price_per_t": "150.00"},
        {"packaging": "bagged", "price_per_t": "158.00"},
    ]
    assert d["purchase_price_per_t"] == "150.00"
    assert d["sale_price_per_t"] is None
    assert d["purchase_counterparty_name"] == "Агро Сует"
    assert d["purchase_payment_mode"] == "prepaid"
    assert d["billing_label"] is None


def test_detail_404(api: TestClient, auth: dict[str, str], w: World) -> None:
    assert api.get("/api/v1/contracts/999999", headers=auth).status_code == 404


def test_search(api: TestClient, auth: dict[str, str], w: World) -> None:
    r = api.get("/api/v1/contracts/search", params={"q": "агро тр", "side": "sale"}, headers=auth)
    assert r.status_code == 200
    assert r.json() == [
        {
            "id": w.sale.id,
            "number": "2026-018",
            "counterparty_name": "Агро Трейд",
            "product": "Пелети",
            "remaining_t": None,
        }
    ]
    by_number = api.get("/api/v1/contracts/search", params={"q": "п-2026"}, headers=auth).json()
    assert [i["number"] for i in by_number] == ["П-2026-007"]
    assert (
        api.get("/api/v1/contracts/search", params={"q": "агро", "side": "purchase"}, headers=auth).json()[0][
            "number"
        ]
        == "П-2026-007"
    )
    w.sale.status = "closed"
    w.session.flush()
    assert api.get("/api/v1/contracts/search", params={"q": "трейд"}, headers=auth).json() == []
    closed = api.get("/api/v1/contracts/search", params={"q": "трейд", "active": "false"}, headers=auth)
    assert len(closed.json()) == 1


def test_contracts_require_auth(api: TestClient) -> None:
    assert api.get("/api/v1/contracts").status_code == 401


def test_check_over_creates_one_card_and_refreshes(w: World) -> None:
    assert check_over(w.session, w.sale) is None
    make_over(w)
    card = check_over(w.session, w.sale)
    assert card is not None
    assert card.kind == CardKind.CONTRACT_OVER.value
    assert card.payload == {
        "contract_id": w.sale.id,
        "contract_number": "2026-018",
        "product": "Пелети",
        "remaining_t": "-66.000",
        "requested_t": "302.400",
        "shortage_t": "66.000",
    }
    again = check_over(w.session, w.sale)
    assert again is not None and again.id == card.id
    cards = w.session.scalars(select(ActionCard).where(ActionCard.kind == CardKind.CONTRACT_OVER.value)).all()
    assert len(cards) == 1

    w.planned("25.200")  # още заявено → същата карта с нови числа
    refreshed = check_over(w.session, w.sale)
    assert refreshed is not None and refreshed.id == card.id
    assert refreshed.payload["shortage_t"] == "91.200"


def test_cancel_trips_supersedes_card(api: TestClient, auth: dict[str, str], w: World) -> None:
    make_over(w)
    check_over(w.session, w.sale)
    assert over_card(w) is not None
    planned = w.session.scalars(select(Trip).order_by(Trip.id)).all()
    for trip in planned[:3]:  # −75.6 т заявени → remaining = +9.6
        r = api.post(f"/api/v1/trips/{trip.id}/cancel", json={"version": trip.version}, headers=auth)
        assert r.status_code == 200, r.text
    card = over_card(w)
    assert card is not None
    assert card.status == CardStatus.SUPERSEDED.value
    assert card.resolved_at is not None
