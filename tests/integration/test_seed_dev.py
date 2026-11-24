"""scripts/seed_dev.py (agro-l2v.12): идемпотентност и съвпадение с Figma."""

from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.cards import ActionCard, CardKind
from app.models.company import NumberSequence
from app.models.identity import User
from app.models.reports import MatchStatus, SupplierReportLine
from app.models.result import Expense
from app.models.trips import Trip
from scripts import seed_dev


@pytest.fixture
def seeded(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("SEED_PASSWORD", "seed-test-pass")
    from app.core.config import get_settings

    get_settings.cache_clear()
    seed_dev.run(db_session)
    return db_session


def _counts(s: Session) -> tuple[int, ...]:
    return tuple(
        s.scalar(select(func.count()).select_from(m)) or 0 for m in (User, Trip, ActionCard, Expense)
    )


def test_seed_twice_no_duplicates(seeded: Session) -> None:
    first = _counts(seeded)
    seed_dev.run(seeded)
    assert _counts(seeded) == first
    assert first[0] == 4


def test_seed_audited_as_seed(seeded: Session) -> None:
    sources = set(seeded.scalars(select(AuditLog.source).distinct()))
    assert "seed" in sources


def test_dashboard_today_matches_figma(seeded: Session) -> None:
    from datetime import date

    rows = seeded.execute(
        select(Trip.status, func.count()).where(Trip.planned_date == date(2026, 11, 12)).group_by(Trip.status)
    ).all()
    assert dict(rows) == {"completed": 4, "planned": 4, "cancelled": 1}


def test_report_is_11_1_1(seeded: Session) -> None:
    rows = seeded.execute(
        select(SupplierReportLine.match_status, func.count()).group_by(SupplierReportLine.match_status)
    ).all()
    assert dict(rows) == {
        MatchStatus.MATCHED.value: 11,
        MatchStatus.UNPLANNED.value: 1,
        MatchStatus.MISSING.value: 1,
    }


def test_open_cards_and_expenses(seeded: Session) -> None:
    kinds = set(seeded.scalars(select(ActionCard.kind).where(ActionCard.status == "open")))
    assert {CardKind.CLIENT_REQUEST, CardKind.SUPPLIER_REPORT, CardKind.CANCELLATION} <= kinds
    assert seeded.scalar(select(func.sum(Expense.amount))) == Decimal("18000.00")
    assert (
        seeded.scalar(select(NumberSequence.next_number).where(NumberSequence.code == "invoice_main")) == 1525
    )
