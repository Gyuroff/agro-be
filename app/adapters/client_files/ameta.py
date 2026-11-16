"""Адаптер „Амета/Пилко“: работният xlsx `Raboten fail` — колона „Място на товарене“ (agro-cmf).

Файлът: един лист; заглавията са в първите редове, редовете на заявката са най-долу. Попълва се САМО
клетката „Място на товарене“ на реда на камиона (името на склада); всичко останало остава непокътнато.
Редът се намира по `source_row` (номерът от префикса `[rN]`, който агентът връща) и се проверява в кода:
„Дата на изпълнение“ на реда трябва да е датата на камиона — иначе реда се пропуска с предупреждение.
"""

import io
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.worksheet.worksheet import Worksheet

from app.adapters.client_files.base import FillResult, LineAssignment

HEADER_SCAN_ROWS = 3
LOAD_PLACE_HEADER = "място на товарене"
EXEC_DATE_HEADER = "дата на изпълнение"


def _find_column(ws: Worksheet, text: str) -> int | None:
    for r in range(1, HEADER_SCAN_ROWS + 1):
        for c in range(1, ws.max_column + 1):
            if text in str(ws.cell(r, c).value or "").casefold():
                return c
    return None


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


class AmetaAdapter:
    key = "ameta"
    label = "Амета/Пилко — „Място на товарене“ в работния xlsx"
    filename_suffix = ".xlsx"

    def fill(self, path: str | Path, lines: list[LineAssignment]) -> FillResult:
        result = FillResult()
        wb = load_workbook(path)  # без data_only: формулите остават формули
        try:
            ws = wb.worksheets[0]
            place_col = _find_column(ws, LOAD_PLACE_HEADER)
            date_col = _find_column(ws, EXEC_DATE_HEADER)
            if place_col is None or date_col is None:
                result.warnings.append(
                    "във файла няма „Място на товарене“/„Дата на изпълнение“ — не се връща"
                )
                return result
            for line in lines:
                label = f"{line.planned_date:%d.%m}" + (f" {line.plate}" if line.plate else "")
                if line.source_row is None or line.warehouse_name is None:
                    result.warnings.append(f"{label}: няма ред във файла или склад — пропуснат")
                    continue
                if _as_date(ws.cell(line.source_row, date_col).value) != line.planned_date:
                    result.warnings.append(f"{label}: ред {line.source_row} е с друга дата — пропуснат")
                    continue
                cell = ws.cell(line.source_row, place_col)
                if isinstance(cell, MergedCell) or cell.value not in (None, ""):
                    result.warnings.append(f"{label}: клетката в ред {line.source_row} е заета — пропуснат")
                    continue
                cell.value = line.warehouse_name
                result.filled += 1
            if result.filled:
                buffer = io.BytesIO()
                wb.save(buffer)
                result.data = buffer.getvalue()
            return result
        finally:
            wb.close()
