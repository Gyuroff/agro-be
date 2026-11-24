"""Количество над един камион → няколко реда без номер (agro-nhp)."""

from datetime import date
from decimal import Decimal

from app.agents.request_parser.schemas import ParsedLine
from app.services.request_intake import _build_lines  # pyright: ignore[reportPrivateUsage]

CAP = Decimal("25.200")
DAY = date(2026, 11, 16)


def _rows(**kw: object):
    return _build_lines([ParsedLine(date=DAY, **kw)], CAP, DAY)  # pyright: ignore[reportArgumentType]


def test_48_t_without_plate_becomes_two_equal_trucks() -> None:
    rows = _rows(qty_t=Decimal("48"))
    assert [r["planned_qty_t"] for r in rows] == [Decimal("24.000")] * 2
    assert all(r["plate_raw"] == "" and "plate" in r["uncertain_fields"] for r in rows)
    assert "2 камиона" in (rows[0]["uncertain_note"] or "")


def test_sum_is_exact_when_not_divisible() -> None:
    rows = _rows(qty_t=Decimal("50"))  # 2 × 25
    assert sum(r["planned_qty_t"] for r in rows) == Decimal("50")
    rows = _rows(qty_t=Decimal("70"))  # 3 камиона
    assert len(rows) == 3 and sum(r["planned_qty_t"] for r in rows) == Decimal("70")


def test_capacity_exactly_or_less_is_one_truck() -> None:
    assert len(_rows(qty_t=CAP)) == 1
    assert len(_rows(qty_t=Decimal("20"))) == 1


def test_line_with_plate_or_own_transport_or_no_qty_is_not_split() -> None:
    assert len(_rows(qty_t=Decimal("48"), plate_raw="CA1234BX")) == 1
    assert len(_rows(qty_t=Decimal("48"), own_transport=True)) == 1
    only = _rows()
    assert len(only) == 1 and only[0]["planned_qty_t"] == CAP
