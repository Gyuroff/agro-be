"""Справка от доставчик в JSON (docs/api.md §4)."""

from datetime import date

from pydantic import BaseModel

from app.schemas.cards import Dec, ReportSubstitute
from app.schemas.emails import EmailAttachmentDTO
from app.schemas.trips import IdName, UtcTime


class ReportCountsDTO(BaseModel):
    matched: int
    unplanned: int
    missing: int


class ReportMatchedDTO(BaseModel):
    line_id: int
    plate: str
    load_date: date | None
    planned_qty_t: Dec
    qty_t: Dec | None


class ReportDTO(BaseModel):
    id: int
    inbound_email_id: int
    supplier: IdName
    received_at: UtcTime
    from_address: str
    attachment: EmailAttachmentDTO | None
    counts: ReportCountsDTO
    needs_decision: list[ReportSubstitute]
    matched: list[ReportMatchedDTO]
    total_rows: int
    total_qty_t: Dec
    confirm_label_count: int
    customer_names: list[str]
    status: str
