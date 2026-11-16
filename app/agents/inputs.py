"""Вход към агентите (docs/llm.md §3): таблици → TSV, PDF → текст, писмо без цитати и подписи.

Към модела отива само извлечен текст — никога бинарни файлове или сурови `.eml`.
"""

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from pypdf import PdfReader

MAX_XLSX_ROWS = 500


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


def xlsx_to_tsv(path: str | Path, max_rows: int = MAX_XLSX_ROWS) -> str:
    """Видимите листове като TSV; празните редове отпадат; общо най-много `max_rows` реда с данни.

    Всеки лист е предшестван от ред `# <име>`. Формулите се четат със записаните им стойности.
    """
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        out: list[str] = []
        rows = 0
        for ws in wb.worksheets:
            if ws.sheet_state != "visible" or rows >= max_rows:
                continue
            out.append(f"# {ws.title}")
            for row in ws.iter_rows(values_only=True):
                cells = [_cell(v) for v in row]
                while cells and not cells[-1]:
                    cells.pop()
                if not cells:
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
