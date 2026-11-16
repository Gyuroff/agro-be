"""Вход към агентите (docs/llm.md §3): таблици → TSV, PDF → текст, писмо без цитати и подписи.

Към модела отива само извлечен текст — никога бинарни файлове или сурови `.eml`.
"""

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from pypdf import PdfReader

MAX_XLSX_ROWS = 500
EXEC_DATE_HEADER = "дата на изпълнение"
HEADER_ROWS = (
    5  # в първите толкова реда се търси заглавието; редове без дата до толкова след него са подзаглавия
)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "да" if value else "не"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == datetime.min.time() else value.isoformat(" ")
    if isinstance(value, date):
        return value.isoformat()
    return re.sub(r"[\t\r\n]+", " ", str(value)).strip()


def date_window(received: date, before_days: int = 0, after_days: int = 28) -> tuple[date, date]:
    """Прозорец на „Дата на изпълнение“ спрямо датата на писмото: заявките са за предстоящи дни."""
    return received - timedelta(days=before_days), received + timedelta(days=after_days)


def _exec_date_column(cells: list[str]) -> int | None:
    for i, cell in enumerate(cells):
        if cell.strip().casefold() == EXEC_DATE_HEADER:
            return i
    return None


def _in_window(cells: list[str], col: int, window: tuple[date, date]) -> bool:
    try:
        day = date.fromisoformat(cells[col][:10])
    except (IndexError, ValueError):
        return False
    return window[0] <= day <= window[1]


def _keep(cells: list[str], col: int, window: tuple[date, date], n: int, header_n: int) -> bool:
    """Ред след заглавието: с дата — по прозореца; без дата — само подзаглавие веднага след заглавието."""
    try:
        date.fromisoformat(cells[col][:10])
    except (IndexError, ValueError):
        return n < header_n + HEADER_ROWS
    return _in_window(cells, col, window)


def xlsx_to_tsv(
    path: str | Path, max_rows: int = MAX_XLSX_ROWS, window: tuple[date, date] | None = None
) -> str:
    """Видимите листове като TSV; празните редове отпадат; общо най-много `max_rows` реда с данни.

    Всеки лист е предшестван от ред `# <име>`. Формулите се четат със записаните им стойности.
    `window`: работните файлове растат с години (1474 реда), а седмицата е най-долу. Ако в първите
    `HEADER_ROWS` реда има колона „Дата на изпълнение“, остават заглавните редове и редовете с дата в
    прозореца. Без такава колона листът се чете както преди (първите `max_rows` реда).
    """
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        out: list[str] = []
        rows = 0
        for ws in wb.worksheets:
            if ws.sheet_state != "visible" or rows >= max_rows:
                continue
            out.append(f"# {ws.title}")
            col: int | None = None
            header_n = 0
            for n, row in enumerate(ws.iter_rows(values_only=True)):
                cells = [_cell(v) for v in row]
                while cells and not cells[-1]:
                    cells.pop()
                if not cells:
                    continue
                if window is not None and col is None and n < HEADER_ROWS:
                    col = _exec_date_column(cells)
                    header_n = n
                elif window is not None and col is not None and not _keep(cells, col, window, n, header_n):
                    continue
                out.append("\t".join(cells))
                rows += 1
                if rows >= max_rows:
                    break
        return "\n".join(out)
    finally:
        wb.close()


def pdf_text(path: str | Path) -> str:
    """Текстовият слой на PDF. Празен низ = няма текст (скан) → извикващият не праща към LLM."""
    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()


_QUOTE_HEADERS = re.compile(
    r"^\s*(?:"
    r"-{2,}\s*(?:original message|оригинално съобщение|forwarded message)\s*-{2,}"
    r"|on .{5,200}wrote:\s*"
    r"|на .{5,200}(?:написа|пише):\s*"
    r"|(?:from|от)\s*:.*"  # начало на цитирана хедър-секция
    r")$",
    re.IGNORECASE,
)
_SIGNATURE = re.compile(
    r"^\s*(?:--\s*|__+\s*|(?:sent from my|изпратено от).*"
    r"|(?:с уважение|поздрави|best regards|regards)[,.!]?\s*)$",
    re.IGNORECASE,
)


def clean_email_text(text: str) -> str:
    """Маха цитирани отговори (`>` и „On … wrote:“/„На … написа:“) и подписа (`-- `, поздрав, „Sent from“).

    Прости правила, не LLM: реже от първия маркер надолу. Редът „От:/From:“ реже само ако идва след
    празен ред (иначе е част от текста). Ако всичко отпадне — връща оригинала, за да не загубим писмото.
    """
    kept: list[str] = []
    prev_blank = True
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        is_header = bool(_QUOTE_HEADERS.match(line))
        if is_header and re.match(r"(?i)^\s*(from|от)\s*:", line) and not prev_blank:
            is_header = False
        if is_header or _SIGNATURE.match(line):
            break
        prev_blank = not stripped
        if stripped.startswith(">"):
            continue
        kept.append(line.rstrip())
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return cleaned or text.strip()
