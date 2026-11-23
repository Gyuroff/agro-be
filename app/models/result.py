"""Резултат: разходи и история преди старта (docs/data-model.md §12)."""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, ForeignKey, Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AuditMixin, Base, Money, enum_check


class ExpenseKind(StrEnum):
    MONTHLY = "monthly"
    ONE_OFF = "one_off"


class ExpenseCategory(AuditMixin, Base):
    __tablename__ = "expense_categories"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


class Expense(AuditMixin, Base):
    __tablename__ = "expenses"
    __table_args__ = (
        enum_check("kind", ExpenseKind),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("extract(day from start_month) = 1", name="start_month_first_day"),
        CheckConstraint(
            "kind <> 'one_off' OR (end_month IS NOT NULL AND end_month = start_month)",
            name="one_off_end_month",
        ),
        Index("ix_expenses_category_id", "category_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("expense_categories.id"), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    start_month: Mapped[date] = mapped_column(Date, nullable=False)
    end_month: Mapped[date | None] = mapped_column(Date, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    category: Mapped["ExpenseCategory"] = relationship("ExpenseCategory", foreign_keys=[category_id])


class MonthlyResultOpening(AuditMixin, Base):
    __tablename__ = "monthly_result_openings"
    __table_args__ = (CheckConstraint("extract(day from month) = 1", name="month_first_day"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    month: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    gross_margin: Mapped[Decimal] = mapped_column(Money, nullable=False)
    expenses: Mapped[Decimal] = mapped_column(Money, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
