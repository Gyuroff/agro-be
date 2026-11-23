"""Модели фактуриране (agro-l2v.11 → agro-l2v.10): uq_invoices_cycle, FK trips.invoice_id."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit_context import AuditContext
from app.models.invoicing import Invoice, InvoiceComparison, InvoiceLine, MegaInvoice
from app.models.masterdata import Counterparty, Product

D = Decimal


def _client(db: Session, eik: str = "111111111") -> Counterparty:
    cp = Counterparty(name="Агро Трейд", legal_name="Агро Трейд ООД", eik=eik)
    db.add(cp)
    db.flush()
    return cp


def _invoice(cp: Counterparty, **kw: object) -> Invoice:
    data: dict[str, object] = {
        "counterparty_id": cp.id,
        "mode": "shadow",
        "billing_date": date(2026, 11, 16),
        "period_from": date(2026, 11, 9),
        "period_to": date(2026, 11, 15),
        "subtotal": D("100.00"),
        "vat_amount": D("20.00"),
        "total": D("120.00"),
    }
    data.update(kw)
    return Invoice(**data)


def test_defaults(db_session: Session, audit_ctx: AuditContext) -> None:
    inv = _invoice(_client(db_session))
    db_session.add(inv)
    db_session.flush()
    db_session.refresh(inv)
    assert (inv.status, inv.currency, inv.mega_export_status) == ("draft", "EUR", "not_started")


def test_two_non_void_invoices_same_cycle_rejected(db_session: Session, audit_ctx: AuditContext) -> None:
    cp = _client(db_session)
    db_session.add(_invoice(cp))
    db_session.flush()
    db_session.add(_invoice(cp, status="issued"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_void_then_new_for_same_cycle_allowed(db_session: Session, audit_ctx: AuditContext) -> None:
    cp = _client(db_session)
    db_session.add_all([_invoice(cp, status="void"), _invoice(cp)])
    db_session.flush()


def test_different_cycles_allowed(db_session: Session, audit_ctx: AuditContext) -> None:
    cp = _client(db_session)
    db_session.add_all([_invoice(cp), _invoice(cp, billing_date=date(2026, 11, 23))])
    db_session.flush()


@pytest.mark.parametrize("overrides", [{"mode": "x"}, {"status": "x"}, {"mega_export_status": "x"}])
def test_invalid_enum(db_session: Session, audit_ctx: AuditContext, overrides: dict[str, object]) -> None:
    db_session.add(_invoice(_client(db_session), **overrides))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_lines_unique_line_no_and_comparison_check(db_session: Session, audit_ctx: AuditContext) -> None:
    cp = _client(db_session)
    product = Product(name="Пелети")
    inv = _invoice(cp)
    db_session.add_all([product, inv])
    db_session.flush()

    def line(no: int) -> InvoiceLine:
        return InvoiceLine(
            invoice_id=inv.id,
            line_no=no,
            product_id=product.id,
            description="Пелети",
            qty_t=D("10.000"),
            unit_price=D("10.00"),
            amount=D("100.00"),
            vat_rate=D("20.00"),
            vat_amount=D("20.00"),
        )

    db_session.add_all([line(1), line(2)])
    db_session.flush()
    db_session.add(line(2))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_comparison_result_check_and_mega_number_unique(db_session: Session, audit_ctx: AuditContext) -> None:
    inv = _invoice(_client(db_session))
    db_session.add_all([inv, MegaInvoice(source="email_copy", number="0000001524")])
    db_session.flush()
    db_session.add(InvoiceComparison(invoice_id=inv.id, result="missing"))
    db_session.flush()
    db_session.add(InvoiceComparison(invoice_id=inv.id, result="bogus"))
    with pytest.raises(IntegrityError):
        db_session.flush()
