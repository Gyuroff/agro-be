"""Резултат (управленска печалба) в JSON (docs/api.md §7)."""

from datetime import date

from pydantic import BaseModel

from app.schemas.cards import Dec


class MtdDTO(BaseModel):
    gross_margin: Dec
    trip_count: int
    expenses: Dec
    coverage_pct: int
    remaining_to_cover: Dec
    net: Dec


class YtdDTO(BaseModel):
    gross_margin: Dec
    expenses: Dec
    net: Dec


class ResultMonthDTO(BaseModel):
    month: str
    is_mtd: bool
    gross_margin: Dec
    expenses: Dec
    net: Dec


class ExpenseLineDTO(BaseModel):
    label: str
    amount: Dec


class ResultDTO(BaseModel):
    month: str
    as_of: date
    mtd: MtdDTO
    ytd: YtdDTO
    months: list[ResultMonthDTO]
    expense_breakdown: list[ExpenseLineDTO]
    expense_total: Dec
