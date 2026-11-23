"""Справки от доставчик (docs/data-model.md §9)."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditMixin, Base, Tonnes, enum_check


class ReportStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    DISCARDED = "discarded"


class MatchStatus(StrEnum):
    MATCHED = "matched"  # Съвпадат
    UNPLANNED = "unplanned"  # Непланиран
    MISSING = "missing"  # Липсва


class LineResolution(StrEnum):
    SUBSTITUTE_ACCEPTED = "substitute_accepted"
    OTHER_CLIENT = "other_client"
    LEFT_UNPLANNED = "left_unplanned"


class SupplierReport(AuditMixin, Base):
    __tablename__ = "supplier_reports"
    __table_args__ = (
        enum_check("status", ReportStatus),
        Index("ix_supplier_reports_supplier_id", "supplier_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    inbound_email_id: Mapped[int] = mapped_column(ForeignKey("inbound_emails.id"), nullable=False)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("counterparties.id"), nullable=False)
    attachment_id: Mapped[int | None] = mapped_column(ForeignKey("email_attachments.id"), nullable=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default=ReportStatus.PENDING.value, server_default=ReportStatus.PENDING.value
    )
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    total_qty_t: Mapped[Decimal] = mapped_column(
        Tonnes, nullable=False, default=Decimal("0"), server_default=text("0")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)


class SupplierReportLine(AuditMixin, Base):
    __tablename__ = "supplier_report_lines"
    __table_args__ = (
        enum_check("match_status", MatchStatus),
        CheckConstraint(
            "resolution IS NULL OR resolution IN ('substitute_accepted','other_client','left_unplanned')",
            name="resolution",
        ),
        Index("ix_supplier_report_lines_report_id", "report_id"),
        Index("ix_supplier_report_lines_trip_id", "trip_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("supplier_reports.id"), nullable=False)
    row_no: Mapped[int | None] = mapped_column(Integer, nullable=True)  # NULL за `missing`
    load_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    plate_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    plate_norm: Mapped[str | None] = mapped_column(Text, nullable=True)
    driver_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    qty_t: Mapped[Decimal | None] = mapped_column(Tonnes, nullable=True)
    warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id"), nullable=True)
    # Ред от файла започва като `unplanned`; `match_lines` го поправя на `matched`.
    match_status: Mapped[str] = mapped_column(
        Text, nullable=False, default=MatchStatus.UNPLANNED.value, server_default=MatchStatus.UNPLANNED.value
    )
    trip_id: Mapped[int | None] = mapped_column(ForeignKey("trips.id"), nullable=True)
    substitute_for_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("supplier_report_lines.id"), nullable=True
    )
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id"), nullable=True)
    uncertain_fields: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default=text("'{}'")
    )
