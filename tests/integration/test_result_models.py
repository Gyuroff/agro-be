"""expense_categories / expenses / monthly_result_openings (agro-l2v.11)."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit_context import AuditContext
from app.models.result import Expense, ExpenseCategory, MonthlyResultOpening


def _category(db: Session) -> ExpenseCategory:
    cat = ExpenseCategory(name="Наем")
    db.add(cat)
    db.flush()
    return cat


def _expense(cat: ExpenseCategory, **kw: object) -> Expense:
    data: dict[str, object] = {
        "category_id": cat.id,
        "kind": "monthly",
        "amount": Decimal("1200.00"),
        "start_month": date(2026, 11, 1),
    }
    data.update(kw)
    return Expense(**data)


def test_valid_monthly_and_one_off(db_session: Session, audit_ctx: AuditContext) -> None:
    cat = _category(db_session)
    db_session.add_all(
        [
            _expense(cat),
            _expense(cat, kind="one_off", end_month=date(2026, 11, 1), description="ремонт на кола"),
        ]
    )
    db_session.flush()


@pytest.mark.parametrize(
    "overrides",
    [
        {"start_month": date(2026, 11, 5)},
        {"kind": "one_off", "end_month": date(2026, 12, 1)},
        {"kind": "one_off", "end_month": None},
        {"amount": Decimal("0")},
        {"kind": "weekly"},
    ],
)
def test_invalid_expense(db_session: Session, audit_ctx: AuditContext, overrides: dict[str, object]) -> None:
    cat = _category(db_session)
    db_session.add(_expense(cat, **overrides))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_category_name_unique(db_session: Session, audit_ctx: AuditContext) -> None:
    _category(db_session)
    db_session.add(ExpenseCategory(name="Наем"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_opening_month_first_day_and_unique(db_session: Session, audit_ctx: AuditContext) -> None:
    ok = MonthlyResultOpening(month=date(2026, 1, 1), gross_margin=Decimal("100"), expenses=Decimal("40"))
    db_session.add(ok)
    db_session.flush()
    db_session.add(
        MonthlyResultOpening(month=date(2026, 1, 1), gross_margin=Decimal("1"), expenses=Decimal("1"))
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_opening_month_not_first_day(db_session: Session, audit_ctx: AuditContext) -> None:
    db_session.add(
        MonthlyResultOpening(month=date(2026, 1, 15), gross_margin=Decimal("1"), expenses=Decimal("1"))
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
