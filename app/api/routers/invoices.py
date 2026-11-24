from fastapi import APIRouter
from fastapi.responses import Response

from app.api.deps import CurrentPrincipal, DbSession
from app.schemas.invoices import InvoiceDTO, IssueInvoiceRequest, VersionDTO
from app.services.invoice_service import InvoiceService, get_invoice
from app.services.invoice_views import invoice_dto

router = APIRouter(tags=["invoices"])


@router.get("/invoices/{invoice_id}", response_model=InvoiceDTO)
def invoice_get(invoice_id: int, _: CurrentPrincipal, session: DbSession) -> InvoiceDTO:
    return invoice_dto(session, get_invoice(session, invoice_id))


@router.get("/invoices/{invoice_id}/pdf")
def invoice_pdf(invoice_id: int, _: CurrentPrincipal, session: DbSession) -> Response:
    data = InvoiceService.pdf_bytes(session, get_invoice(session, invoice_id))
    return Response(data, media_type="application/pdf")


@router.post("/invoices/{invoice_id}/issue", response_model=VersionDTO)
def invoice_issue(
    invoice_id: int, body: IssueInvoiceRequest, _: CurrentPrincipal, session: DbSession
) -> VersionDTO:
    invoice = InvoiceService.issue(session, invoice_id, body.version)
    session.refresh(invoice)
    return VersionDTO(version=invoice.version)


@router.post("/invoices/{invoice_id}/resend", response_model=VersionDTO)
def invoice_resend(invoice_id: int, _: CurrentPrincipal, session: DbSession) -> VersionDTO:
    invoice = InvoiceService.resend(session, invoice_id)
    session.refresh(invoice)
    return VersionDTO(version=invoice.version)
