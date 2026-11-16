from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from app.adapters.client_files import get_adapter
from app.adapters.client_files.base import LineAssignment


def book(tmp_path: Path, place_header: bool = True) -> Path:
    """Като `Raboten fail`: заглавия в ред 1–2, „Място на товарене“ в колона I, данните от ред 3."""
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws["E1"], ws["G1"] = "Количество", "Дата на изпълнение"
    if place_header:
        ws["I2"] = "Място на товарене"
    rows = [(date(2026, 11, 17), "Насипни"), (date(2026, 11, 18), "Бегове"), (date(2026, 11, 18), "Насипни")]
    for i, (day, pack) in enumerate(rows, start=3):
        ws.cell(i, 5, "24т")
        ws.cell(i, 7, day)
        ws.cell(i, 8, pack)
        ws.cell(i, 13, f"=+E{i}*430/1000")
    ws["I5"] = "Разделна"  # вече попълнено от човек
    path = tmp_path / "raboten.xlsx"
    wb.save(path)
    return path


def line(row: int | None, day: date, warehouse: str | None = "Попово") -> LineAssignment:
    return LineAssignment(row, day, "", None, Decimal("24"), True, warehouse, "Юропак")


def test_fills_only_load_place_and_keeps_formulas(tmp_path: Path) -> None:
    adapter = get_adapter("ameta")
    assert adapter is not None
    res = adapter.fill(book(tmp_path), [line(3, date(2026, 11, 17)), line(4, date(2026, 11, 18), "Разделна")])
    assert res.filled == 2 and res.data is not None and res.warnings == []
    ws = load_workbook(BytesIO(res.data)).worksheets[0]
    assert (ws["I3"].value, ws["I4"].value) == ("Попово", "Разделна")
    assert ws["M3"].value == "=+E3*430/1000" and ws["H4"].value == "Бегове"  # нищо друго не е пипнато


def test_never_guesses(tmp_path: Path) -> None:
    adapter = get_adapter("ameta")
    assert adapter is not None
    res = adapter.fill(
        book(tmp_path),
        [
            line(3, date(2026, 11, 19)),  # ред 3 е с друга дата
            line(None, date(2026, 11, 17)),  # няма ред във файла
            line(4, date(2026, 11, 18), None),  # няма склад („избери“)
            line(5, date(2026, 11, 18)),  # клетката вече е попълнена от човек
        ],
    )
    assert res.filled == 0 and res.data is None and len(res.warnings) == 4


def test_file_without_the_column_is_not_returned(tmp_path: Path) -> None:
    adapter = get_adapter("ameta")
    assert adapter is not None
    res = adapter.fill(book(tmp_path, place_header=False), [line(3, date(2026, 11, 17))])
    assert res.data is None and "не се връща" in res.warnings[0]


def test_choices_for_settings_dropdown() -> None:
    from app.adapters.client_files import adapter_choices

    assert [key for key, _ in adapter_choices()] == ["ameta"]


def test_registry() -> None:
    assert get_adapter(None) is None
    with pytest.raises(ValueError):
        get_adapter("няма-такъв")
