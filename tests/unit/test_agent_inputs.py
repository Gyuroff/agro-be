from pathlib import Path

from openpyxl import Workbook

from app.agents.inputs import clean_email_text, pdf_text, xlsx_to_tsv


def _book(tmp_path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Заявка"
    ws.append(["Номер", "Шофьор", "Тонове"])
    ws.append(["СА 1234 КВ", "Иван Петров", 24.0])
    ws.append([None, None, None])
    ws.append(["В 5678 ТХ", "Стоян\tГеоргиев", 24.86])
    hidden = wb.create_sheet("Скрит")
    hidden.append(["тайна"])
    hidden.sheet_state = "hidden"
    path = tmp_path / "zayavka.xlsx"
    wb.save(path)
    return path


def test_xlsx_to_tsv_cyrillic_visible_sheets_only(tmp_path: Path) -> None:
    tsv = xlsx_to_tsv(_book(tmp_path))
    assert tsv.splitlines() == [
        "# Заявка",
        "Номер\tШофьор\tТонове",
        "СА 1234 КВ\tИван Петров\t24",
        "В 5678 ТХ\tСтоян Георгиев\t24.86",
    ]
    assert "тайна" not in tsv


def test_xlsx_to_tsv_row_limit(tmp_path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    for i in range(700):
        ws.append([i, "ред"])
    path = tmp_path / "big.xlsx"
    wb.save(path)
    lines = xlsx_to_tsv(path).splitlines()
    assert len(lines) == 501  # заглавен ред на листа + 500 реда


def test_clean_email_removes_quote_and_signature() -> None:
    text = (
        "Здравейте,\nпрепращам заявката.\n\n> стар текст\n\nПоздрави,\nИван\n\n"
        "На 12.11.2026 г. Петър написа:\n> нещо"
    )
    assert clean_email_text(text) == "Здравейте,\nпрепращам заявката."


def test_clean_email_english_markers() -> None:
    text = "Please send 3 trucks.\n\nOn Mon, 12 Nov 2026 at 10:00, A <a@b.bg> wrote:\n> old"
    assert clean_email_text(text) == "Please send 3 trucks."
    assert clean_email_text("Hello\n-- \nJohn") == "Hello"


def test_clean_email_keeps_inline_from_and_never_returns_empty() -> None:
    assert "От: склад" in clean_email_text("Камионът тръгва.\nОт: склад Русе")
    assert clean_email_text("> само цитат") == "> само цитат"


def _pdf_with_text(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


def test_pdf_text_reads_text_layer(tmp_path: Path) -> None:
    path = tmp_path / "inv.pdf"
    path.write_bytes(_pdf_with_text("Invoice 0000123 total 120.50"))
    assert pdf_text(path) == "Invoice 0000123 total 120.50"


def test_pdf_text_empty_without_text_layer(tmp_path: Path) -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    path = tmp_path / "scan.pdf"
    with path.open("wb") as f:
        writer.write(f)
    assert pdf_text(path) == ""


def test_xlsx_window_keeps_headers_and_week_rows_of_big_file(tmp_path: Path) -> None:
    from datetime import date

    from openpyxl import Workbook

    from app.agents.inputs import date_window

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["Заявка", None, "Клиент", None, "Количество", "Транспорт", "Дата на изпълнение"])
    ws.append(["№", "Дата", "Ферма", "Фирма", None, None, None, None, "Място на товарене"])
    for i in range(1, 1500):  # 1499 стари реда, както в реалния файл
        ws.append([i, date(2022, 9, 15), "Ферма", "Клиент", 21820, "не", date(2022, 9, 20)])
    ws.append(
        [1500, date(2026, 11, 8), "Глоджево", "Пилко", "24т", None, date(2026, 11, 16), None, "транспорт Ива"]
    )
    ws.append([1501, date(2026, 11, 8), "Беловец", "Пилко", "24т", None, date(2026, 11, 17)])
    ws.append([1502, date(2026, 11, 8), "Далечен", "Пилко", "24т", None, date(2027, 3, 1)])
    path = tmp_path / "big.xlsx"
    wb.save(path)

    tsv = xlsx_to_tsv(path, window=date_window(date(2026, 11, 11)))
    lines = tsv.split("\n")
    assert lines[0] == "# Sheet" and "Дата на изпълнение" in lines[1] and lines[2].startswith("№")
    assert [ln.split("\t")[2] for ln in lines[3:]] == ["Глоджево", "Беловец"]
    assert "транспорт Ива" in tsv

    assert xlsx_to_tsv(path, max_rows=10).count("\n") == 10  # без прозорец — както преди
