"""GET /result (agro-br9.4): марж, разходи, покритие, YTD, по месеци."""

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
from app.models.result import Expense, ExpenseCategory, MonthlyResultOpening
from app.models.trips import CancelSource
from app.services.trips import TripService
from app.services.users import UserService
from tests.integration.test_auth import EMAIL, PASSWORD, bearer, login
from tests.integration.test_trips import World

D = Decimal
TODAY = date(2026, 11, 12)
NOV = date(2026, 11, 1)


@pytest.fixture
def w(db_session: Session, audit_ctx: AuditContext, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setattr(clock, "today_sofia", lambda: TODAY)
    return World(db_session)


@pytest.fixture
def api(db_session: Session, audit_ctx: AuditContext) -> Iterator[TestClient]:
    UserService.create(
        db_session, email=EMAIL, full_name="Георги Гюров", short_name="Георги", password=PASSWORD
    )
    db_session.flush()
    app = create_app()

    def override_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        yield client


@pytest.fixture
def auth(api: TestClient) -> dict[str, str]:
    return bearer(login(api).json()["access_token"])


def complete(w: World, qty: str, load_date: date) -> None:
    trip = w.planned(qty)
    TripService(w.session).complete(trip, actual_qty_t=D(qty), load_date=load_date)


def expense(db: Session, cat: ExpenseCategory, amount: str, **kw: object) -> None:
    data: dict[str, object] = {
        "category_id": cat.id,
        "kind": "monthly",
        "amount": D(amount),
        "start_month": NOV,
    }
    data.update(kw)
    db.add(Expense(**data))


def figma_month(w: World) -> ExpenseCategory:
    """Продажба 210, покупка 150 → 60 €/т; 204 т = 12 240. Разходи 12 000 + еднократно 1 000 + 5 000."""
    complete(w, "102.000", TODAY)
    complete(w, "102.000", date(2026, 11, 3))
    salaries, rent = ExpenseCategory(name="Заплати", sort_order=1), ExpenseCategory(name="Наем", sort_order=2)
    w.session.add_all([salaries, rent])
    w.session.flush()
    expense(w.session, salaries, "12000.00")
    expense(w.session, rent, "5000.00")
    expense(w.session, rent, "1000.00", kind="one_off", end_month=NOV, description="ремонт на кола")
    w.session.flush()
    return salaries


def test_figma_example(api: TestClient, auth: dict[str, str], w: World) -> None:
    figma_month(w)
    body = api.get("/api/v1/result", headers=auth).json()
    assert body["month"] == "2026-11"
    assert body["as_of"] == "2026-11-12"
    assert body["mtd"] == {
        "gross_margin": "12240.00",
        "trip_count": 2,
        "expenses": "18000.00",
        "coverage_pct": 68,
        "remaining_to_cover": "5760.00",
        "net": "-5760.00",
    }
    assert body["expense_total"] == "18000.00"
    assert body["expense_breakdown"] == [
        {"label": "Заплати", "amount": "12000.00"},
        {"label": "Наем", "amount": "5000.00"},
        {"label": "Еднократно: ремонт на кола", "amount": "1000.00"},
    ]
    assert body["months"][0]["is_mtd"] is True


def test_expense_after_month_end_is_excluded(api: TestClient, auth: dict[str, str], w: World) -> None:
    cat = figma_month(w)
    expense(w.session, cat, "700.00", start_month=date(2026, 6, 1), end_month=date(2026, 10, 1))
    expense(w.session, cat, "900.00", start_month=date(2026, 12, 1))
    w.session.flush()
    body = api.get("/api/v1/result", headers=auth).json()
    assert body["mtd"]["expenses"] == "18000.00"


def test_cancelled_and_other_month_trips_do_not_count(
    api: TestClient, auth: dict[str, str], w: World
) -> None:
    figma_month(w)
    complete(w, "50.000", date(2026, 10, 30))  # друг месец, по load_date
    cancelled = w.planned("30.000")
    TripService(w.session).cancel(cancelled, source=CancelSource.USER, reason="отказан")
    body = api.get("/api/v1/result", headers=auth).json()
    assert body["mtd"]["gross_margin"] == "12240.00"
    assert body["mtd"]["trip_count"] == 2
    october = next(m for m in body["months"] if m["month"] == "2026-10")
    assert october["gross_margin"] == "3000.00"


def test_openings_before_first_trip_month_and_ytd(api: TestClient, auth: dict[str, str], w: World) -> None:
    figma_month(w)
    for month, margin, spent in ((1, "20000.00", "15000.00"), (2, "30000.00", "16000.00")):
        w.session.add(
            MonthlyResultOpening(month=date(2026, month, 1), gross_margin=D(margin), expenses=D(spent))
        )
    w.session.flush()
    body = api.get("/api/v1/result", headers=auth).json()
    assert body["months"][0]["month"] == "2026-11"
    assert body["months"][-1]["month"] == "2026-01"
    jan = body["months"][-1]
    assert (jan["gross_margin"], jan["expenses"], jan["net"], jan["is_mtd"]) == (
        "20000.00",
        "15000.00",
        "5000.00",
        False,
    )
    assert body["ytd"] == {"gross_margin": "62240.00", "expenses": "49000.00", "net": "13240.00"}


def test_requires_auth_and_validates_month(api: TestClient, auth: dict[str, str], w: World) -> None:
    assert api.get("/api/v1/result").status_code == 401
    assert api.get("/api/v1/result", params={"month": "2026-13"}, headers=auth).status_code in (400, 422)
