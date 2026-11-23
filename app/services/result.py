"""Резултат: марж, разходи, покритие (data-model.md §12, api.md §7). Само четене, суми без ДДС."""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import Date, cast, func, select
from sqlalchemy.orm import Session

from app.core import clock
from app.models.result import Expense, ExpenseCategory, ExpenseKind, MonthlyResultOpening
from app.models.trips import Trip, TripStatus
from app.schemas.result import ExpenseLineDTO, MtdDTO, ResultDTO, ResultMonthDTO, YtdDTO

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def month_start(day: date) -> date:
    return day.replace(day=1)


def _next_month(month: date) -> date:
    return date(month.year + (month.month == 12), month.month % 12 + 1, 1)


def coverage_pct(margin: Decimal, expenses: Decimal) -> int:
    """floor(марж / разходи × 100). Без разходи няма какво да се покрива → 100; отрицателен марж → 0."""
    if expenses <= 0:
        return 100
    if margin <= 0:
        return 0
    return int(margin * 100 // expenses)


class ResultService:
    def __init__(self, session: Session) -> None:
        self.session = session

    # --- марж от курсове ---------------------------------------------------------------------
    def _first_trip_month(self, today: date) -> date:
        first = self.session.scalar(
            select(func.min(Trip.load_date)).where(Trip.status == TripStatus.COMPLETED.value)
        )
        return month_start(first) if first else month_start(today)

    def _trip_margins(self, start: date, end: date) -> dict[date, tuple[Decimal, int]]:
        """месец → (марж, брой) за completed курсове с load_date в [start, end)."""
        month = cast(func.date_trunc("month", Trip.load_date), Date)
        margin = func.round(Trip.actual_qty_t * (Trip.sale_price_per_t - Trip.purchase_price_per_t), 2)
        rows = self.session.execute(
            select(month, func.coalesce(func.sum(margin), 0), func.count(Trip.id))
            .where(
                Trip.status == TripStatus.COMPLETED.value,
                Trip.load_date >= start,
                Trip.load_date < end,
                Trip.actual_qty_t.is_not(None),
                Trip.sale_price_per_t.is_not(None),
                Trip.purchase_price_per_t.is_not(None),
            )
            .group_by(month)
        ).all()
        return {m: (Decimal(total).quantize(CENT), count) for m, total, count in rows}

    # --- разходи -----------------------------------------------------------------------------
    def _expenses_for(self, month: date) -> list[tuple[Expense, str]]:
        rows = self.session.execute(
            select(Expense, ExpenseCategory.name)
            .join(ExpenseCategory, ExpenseCategory.id == Expense.category_id)
            .where(
                Expense.start_month <= month,
                (
                    (Expense.kind == ExpenseKind.MONTHLY.value)
                    & (Expense.end_month.is_(None) | (Expense.end_month >= month))
                )
                | ((Expense.kind == ExpenseKind.ONE_OFF.value) & (Expense.start_month == month)),
            )
            .order_by(Expense.kind, ExpenseCategory.sort_order, ExpenseCategory.id, Expense.id)
        ).all()
        return [(e, name) for e, name in rows]

    def _breakdown(self, month: date) -> list[ExpenseLineDTO]:
        lines: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for expense, category in self._expenses_for(month):
            if expense.kind == ExpenseKind.ONE_OFF.value:
                label = f"Еднократно: {expense.description}" if expense.description else "Еднократно"
            else:
                label = category
            lines[label] += expense.amount
        return [ExpenseLineDTO(label=label, amount=amount) for label, amount in lines.items()]

    # --- сглобяване --------------------------------------------------------------------------
    def build(self, month: date | None = None) -> ResultDTO:
        today = clock.today_sofia()
        month = month_start(month or today)
        year_start = date(month.year, 1, 1)
        first_trip_month = self._first_trip_month(today)
        margins = self._trip_margins(year_start, min(_next_month(month), today + timedelta(days=1)))
        openings = {
            o.month: o
            for o in self.session.scalars(
                select(MonthlyResultOpening).where(
                    MonthlyResultOpening.month >= year_start, MonthlyResultOpening.month <= month
                )
            )
        }

        values: dict[date, tuple[Decimal, Decimal, int]] = {}
        cursor = year_start
        while cursor <= month:
            if cursor < first_trip_month:
                opening = openings.get(cursor)
                values[cursor] = (opening.gross_margin, opening.expenses, 0) if opening else (ZERO, ZERO, 0)
            else:
                gross, count = margins.get(cursor, (ZERO, 0))
                spent = sum((e.amount for e, _ in self._expenses_for(cursor)), ZERO)
                values[cursor] = (gross, spent, count)
            cursor = _next_month(cursor)

        gross, spent, count = values[month]
        remaining = max(spent - gross, ZERO)
        breakdown = [] if month < first_trip_month else self._breakdown(month)
        return ResultDTO(
            month=month.strftime("%Y-%m"),
            as_of=today,
            mtd=MtdDTO(
                gross_margin=gross,
                trip_count=count,
                expenses=spent,
                coverage_pct=coverage_pct(gross, spent),
                remaining_to_cover=remaining,
                net=gross - spent,
            ),
            ytd=YtdDTO(
                gross_margin=sum((v[0] for v in values.values()), ZERO),
                expenses=sum((v[1] for v in values.values()), ZERO),
                net=sum((v[0] - v[1] for v in values.values()), ZERO),
            ),
            months=[
                ResultMonthDTO(
                    month=m.strftime("%Y-%m"),
                    is_mtd=m == month_start(today),
                    gross_margin=g,
                    expenses=x,
                    net=g - x,
                )
                for m, (g, x, _) in sorted(values.items(), reverse=True)
            ],
            expense_breakdown=breakdown,
            expense_total=spent,
        )
