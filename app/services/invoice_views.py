"""`GET /invoices/{id}` (docs/api.md §8)."""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.invoicing.numbers import format_invoice_number, get_number_provider
from app.models.identity import Actor
from app.models.invoicing import Invoice, InvoiceMode, InvoiceStatus, InvoiceTrip, MegaExport
from app.models.trips import Trip
from app.schemas.invoices import (
    InvoiceDTO,
    InvoiceLineDTO,
    InvoiceStepDTO,
    InvoiceTripsDTO,
    RecipientDTO,
)
from app.services.invoice_service import invoice_lines
from app.services.trip_views import trip_list_items


def _next_number_display() -> str | None:
    """Прогнозният номер е само подсказка; без връзка с Мега екранът се показва без него."""
    try:
        return format_invoice_number(get_number_provider().next_number())
    except DomainError:
        return None


def _actor_name(session: Session, actor_id: int | None) -> str | None:
    if actor_id is None:
        return None
    actor = session.get(Actor, actor_id)
    return actor.display_name if actor is not None else None


def invoice_dto(session: Session, invoice: Invoice) -> InvoiceDTO:
    lines = invoice_lines(session, invoice)
    trips = list(
        session.scalars(
            select(Trip)
            .join(InvoiceTrip, InvoiceTrip.trip_id == Trip.id)
            .where(InvoiceTrip.invoice_id == invoice.id)
            .order_by(Trip.load_date, Trip.id)
        )
    )
    qty = sum((t.actual_qty_t or Decimal(0) for t in trips), Decimal(0))
    issued = invoice.status == InvoiceStatus.ISSUED.value
    last_export = session.scalar(
        select(MegaExport).where(MegaExport.invoice_id == invoice.id).order_by(MegaExport.attempt_no.desc())
    )
    steps = [
        InvoiceStepDTO(
            key="issued",
            done=issued,
            at=invoice.issued_at,
            by=_actor_name(session, invoice.issued_by_id),
        ),
        InvoiceStepDTO(
            key="emailed",
            done=invoice.emailed_at is not None,
            at=invoice.emailed_at,
            to=invoice.recipient_email,
        ),
        InvoiceStepDTO(
            key="mega_export",
            done=invoice.mega_export_status == "ok",
            at=invoice.mega_exported_at,
            error=last_export.error if last_export is not None and last_export.status == "failed" else None,
        ),
    ]
    terms = (
        (invoice.due_date - (invoice.issue_date or invoice.billing_date)).days if invoice.due_date else None
    )
    draft_erp = invoice.status == InvoiceStatus.DRAFT.value and invoice.mode == InvoiceMode.ERP.value
    return InvoiceDTO(
        id=invoice.id,
        mode=invoice.mode,
        status=invoice.status,
        number_display=format_invoice_number(invoice.number) if invoice.number is not None else None,
        next_number_display=_next_number_display() if draft_erp else None,
        issue_date=invoice.issue_date,
        tax_event_date=invoice.tax_event_date,
        due_date=invoice.due_date,
        payment_terms_days=terms,
        recipient=RecipientDTO(
            name=invoice.recipient_name,
            eik=invoice.recipient_eik,
            vat_number=invoice.recipient_vat_number,
            email=invoice.recipient_email,
        ),
        period_from=invoice.period_from,
        period_to=invoice.period_to,
        lines=[
            InvoiceLineDTO(
                line_no=ln.line_no,
                description=ln.description,
                qty_t=ln.qty_t,
                unit_price=ln.unit_price,
                amount=ln.amount,
            )
            for ln in lines
        ],
        subtotal=invoice.subtotal,
        vat_rate=lines[0].vat_rate if lines else Decimal(0),
        vat_amount=invoice.vat_amount,
        total=invoice.total,
        trips=InvoiceTripsDTO(count=len(trips), qty_t=qty, items=trip_list_items(session, trips)),
        steps=steps,
        version=invoice.version,
    )
