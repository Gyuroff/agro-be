"""Четене на справка за екран 3 (docs/api.md §4)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.domain.plates import format_plate
from app.models.mail import EmailAttachment, InboundEmail
from app.models.masterdata import Counterparty, Product, Warehouse
from app.models.reports import LineResolution, MatchStatus, SupplierReport, SupplierReportLine
from app.models.trips import Trip
from app.schemas.cards import ReportSubstitute
from app.schemas.reports import ReportCountsDTO, ReportDTO, ReportMatchedDTO
from app.schemas.trips import IdName
from app.services.email_views import _attachment_dto


def get_report(session: Session, report_id: int) -> SupplierReport:
    report = session.get(SupplierReport, report_id)
    if report is None:
        raise DomainError("not_found", "Справката не е намерена.", http_status=404)
    return report


def substitutes(
    session: Session, lines: list[SupplierReportLine], *, only_open: bool = True
) -> list[ReportSubstitute]:
    """Предложенията „Сменен камион? X вместо Y“ (същият обект е в `payload.substitutes` на картата)."""
    by_id = {line.id: line for line in lines}
    out: list[ReportSubstitute] = []
    for line in lines:
        if line.match_status != MatchStatus.UNPLANNED.value or line.substitute_for_line_id is None:
            continue
        if only_open and line.resolution is not None:
            continue
        missing = by_id.get(line.substitute_for_line_id)
        trip = session.get(Trip, missing.trip_id) if missing and missing.trip_id else None
        if trip is None or line.load_date is None or line.qty_t is None or not line.plate_raw:
            continue
        customer = session.get(Counterparty, trip.customer_id) if trip.customer_id else None
        product = session.get(Product, trip.product_id)
        warehouse_id = line.warehouse_id or trip.warehouse_id
        warehouse = session.get(Warehouse, warehouse_id) if warehouse_id else None
        out.append(
            ReportSubstitute(
                unplanned_line_id=line.id,
                missing_line_id=line.substitute_for_line_id,
                customer=customer.name if customer else "—",
                date=line.load_date,
                product=product.name if product else "—",
                new_plate=format_plate(line.plate_raw),
                new_qty_t=line.qty_t,
                warehouse_name=warehouse.name if warehouse else None,
                old_plate=trip.plate_display,
                old_planned_qty_t=trip.planned_qty_t,
            )
        )
    return out


def report_dto(session: Session, report: SupplierReport) -> ReportDTO:
    lines = list(
        session.scalars(
            select(SupplierReportLine)
            .where(SupplierReportLine.report_id == report.id)
            .order_by(SupplierReportLine.row_no.nulls_last(), SupplierReportLine.id)
        )
    )
    email = session.get(InboundEmail, report.inbound_email_id)
    supplier = session.get(Counterparty, report.supplier_id)
    attachment = session.get(EmailAttachment, report.attachment_id) if report.attachment_id else None
    assert email is not None and supplier is not None

    def count(status: MatchStatus) -> int:
        return sum(1 for line in lines if line.match_status == status.value)

    matched: list[ReportMatchedDTO] = []
    customer_ids: list[int] = []
    for line in lines:
        trip = session.get(Trip, line.trip_id) if line.trip_id else None
        if trip is not None and trip.customer_id is not None and trip.customer_id not in customer_ids:
            customer_ids.append(trip.customer_id)
        if line.match_status == MatchStatus.MATCHED.value and trip is not None:
            matched.append(
                ReportMatchedDTO(
                    line_id=line.id,
                    plate=trip.plate_display,
                    load_date=line.load_date,
                    planned_qty_t=trip.planned_qty_t,
                    qty_t=line.qty_t,
                )
            )
    accepted = sum(
        1
        for line in lines
        if line.match_status == MatchStatus.UNPLANNED.value
        and line.resolution == LineResolution.SUBSTITUTE_ACCEPTED.value
    )
    names = [cp.name for cid in customer_ids if (cp := session.get(Counterparty, cid)) is not None]
    return ReportDTO(
        id=report.id,
        inbound_email_id=report.inbound_email_id,
        supplier=IdName(id=supplier.id, name=supplier.name),
        received_at=email.received_at,
        from_address=email.from_address,
        attachment=_attachment_dto(attachment) if attachment else None,
        counts=ReportCountsDTO(
            matched=count(MatchStatus.MATCHED),
            unplanned=count(MatchStatus.UNPLANNED),
            missing=count(MatchStatus.MISSING),
        ),
        needs_decision=substitutes(session, lines),
        matched=matched,
        total_rows=report.total_rows,
        total_qty_t=report.total_qty_t,
        confirm_label_count=len(matched) + accepted,
        customer_names=names,
        status=report.status,
    )
