"""PDF на фактура (Jinja2 + WeasyPrint). Чернова → воден знак „ЧЕРНОВА“; номер с 10 знака."""

from collections.abc import Sequence
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.invoicing.numbers import format_invoice_number
from app.models.invoicing import Invoice, InvoiceLine, InvoiceStatus

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
)


def _date(value: object) -> str | None:
    return value.strftime("%d.%m.%Y") if hasattr(value, "strftime") else None  # type: ignore[union-attr]


def render_invoice_html(invoice: Invoice, lines: Sequence[InvoiceLine]) -> str:
    vat_rate = lines[0].vat_rate if lines else 0
    return _env.get_template("invoice.html").render(
        draft=invoice.status != InvoiceStatus.ISSUED.value,
        number_display=format_invoice_number(invoice.number) if invoice.number is not None else None,
        issue_date=_date(invoice.issue_date),
        tax_event_date=_date(invoice.tax_event_date),
        due_date=_date(invoice.due_date),
        period_from=_date(invoice.period_from),
        period_to=_date(invoice.period_to),
        supplier={
            "name": invoice.supplier_legal_name,
            "eik": invoice.supplier_eik,
            "vat_number": invoice.supplier_vat_number,
            "address": invoice.supplier_address,
            "mol": invoice.supplier_mol,
            "iban": invoice.supplier_iban,
        },
        recipient={
            "name": invoice.recipient_name,
            "eik": invoice.recipient_eik,
            "vat_number": invoice.recipient_vat_number,
            "address": invoice.recipient_address,
            "mol": invoice.recipient_mol,
        },
        lines=[
            {
                "no": line.line_no,
                "description": line.description,
                "qty_t": f"{line.qty_t:.3f}",
                "unit_price": f"{line.unit_price:.2f}",
                "amount": f"{line.amount:.2f}",
            }
            for line in lines
        ],
        subtotal=f"{invoice.subtotal:.2f}",
        vat_rate=f"{vat_rate:.2f}",
        vat_amount=f"{invoice.vat_amount:.2f}",
        total=f"{invoice.total:.2f}",
        currency=invoice.currency,
    )


def render_invoice_pdf(invoice: Invoice, lines: Sequence[InvoiceLine]) -> bytes:
    from weasyprint import HTML  # късен импорт: нужни са системни библиотеки (pango)

    data = HTML(string=render_invoice_html(invoice, lines)).write_pdf()
    assert data is not None
    return data
