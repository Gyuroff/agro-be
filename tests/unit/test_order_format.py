from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.domain.order_format import render_body, render_table


def line(day: int, plate: str, qty: str, packaging: str | None, own: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        planned_date=date(2026, 11, day), plate_raw=plate, driver_name=None, planned_qty_t=Decimal(qty),
        packaging=packaging, own_transport=own, truck_type=None,
    )  # fmt: skip


LINES = [
    line(19, "СА1234ВХ", "25.2", "bulk"),
    line(19, "", "25.2", "bulk", own=True),
    line(20, "СВ7890ТР", "20", "bagged"),
]


def test_per_truck_marks_own_transport() -> None:
    table = render_table("per_truck", LINES)
    assert "СА 1234 ВХ" in table and "транспорт Ива" in table


def test_per_day_groups_by_date_and_kind() -> None:
    assert render_table("per_day", LINES).splitlines()[1:] == [
        "19.11.2026 | гондола, насипни | 1 | 25.200",  # наш транспорт → гондола
        "19.11.2026 | насипни | 1 | 25.200",
        "20.11.2026 | опаковани | 1 | 20.000",
    ]


def test_per_packaging_count_and_free_schedule() -> None:
    assert render_table("per_packaging_count", LINES).splitlines()[1:] == [
        "насипни | 2 | 50.400",
        "опаковани | 1 | 20.000",
    ]
    assert render_table("free_schedule", LINES).splitlines() == [
        "19.11 — 2 камиона, 50.400 т",
        "20.11 — 1 камиона, 20.000 т",
    ]


def test_body_has_greeting_and_total_and_unknown_format_falls_back() -> None:
    body = render_body("nope", "Здравейте,", LINES)
    assert body.startswith("Здравейте,") and body.endswith("Общо: 3 камиона, 70.400 т")
    assert "Камион" in body


def test_truck_type_default_gondola_for_own_transport_and_manual_override() -> None:
    assert render_table("per_day", LINES, "фуражовоз").splitlines()[1:] == [
        "19.11.2026 | гондола, насипни | 1 | 25.200",
        "19.11.2026 | фуражовоз, насипни | 1 | 25.200",
        "20.11.2026 | фуражовоз, опаковани | 1 | 20.000",
    ]
    LINES[0].truck_type = "самосвал"  # ръчна промяна в писмото
    try:
        assert "самосвал, насипни" in render_table("per_day", LINES, "фуражовоз")
        assert "СА 1234 ВХ (самосвал)" in render_table("per_truck", LINES, "фуражовоз")
        assert "транспорт Ива (гондола)" in render_table("per_truck", LINES, "фуражовоз")
    finally:
        LINES[0].truck_type = None
