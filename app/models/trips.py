"""Заявки и курсове (docs/data-model.md §8).

Колоните към още несъздадени таблици (`inbound_emails`, `outbound_emails`, `agent_runs`,
`supplier_report_lines`, `invoices`) са `BIGINT` без FK — FK се добавя в миграцията на съответната
задача (l2v.6 / l2v.8 / l2v.9 / l2v.10), както при `actors.user_id`.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditMixin, Base, CreatedMixin, PricePerT, Tonnes, enum_check


class RequestStatus(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"  # натиснато „Изпрати“, тече прозорецът за „Отмени“
    SENT = "sent"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class TripStatus(StrEnum):
    PLANNED = "planned"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    UNPLANNED = "unplanned"


class TripOrigin(StrEnum):
    REQUEST = "request"
    REPORT_UNPLANNED = "report_unplanned"
    REPORT_OTHER_CLIENT = "report_other_client"


class CancelSource(StrEnum):
    EMAIL = "email"
    USER = "user"


class TripEventKind(StrEnum):
    REQUESTED = "requested"
    SENT_TO_SUPPLIER = "sent_to_supplier"
    PLATE_CHANGED = "plate_changed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    LINKED = "linked"
    INVOICED = "invoiced"


class ClientRequest(AuditMixin, Base):
    __tablename__ = "client_requests"
    __table_args__ = (enum_check("status", RequestStatus),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    inbound_email_id: Mapped[int] = mapped_column(ForeignKey("inbound_emails.id"), nullable=False)
    sale_contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id"), nullable=True)
    period_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default=RequestStatus.DRAFT.value, server_default=RequestStatus.DRAFT.value
    )
    outbound_email_id: Mapped[int | None] = mapped_column(ForeignKey("outbound_emails.id"), nullable=True)
    # Писмото „Потвърждавам графика“ към клиента (agro-cmf) и xlsx, от който са взети редовете.
    confirmation_outbound_id: Mapped[int | None] = mapped_column(
        ForeignKey("outbound_emails.id", name="fk_client_requests_confirmation_outbound_id"), nullable=True
    )
    source_attachment_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_attachments.id"), nullable=True
    )
    agent_run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # FK → agent_runs (l2v.9)
    agent_notes: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)


class ClientRequestSupplierDraft(AuditMixin, Base):
    """Отмяна на писмото по доставчик (data-model §8): ред само при редакция; NULL = подразбиращото се.

    `version` е от `AuditMixin` (оптимистично заключване): ORM го вдига при всеки UPDATE.
    """

    __tablename__ = "client_request_supplier_drafts"
    __table_args__ = (UniqueConstraint("request_id", "counterparty_id", name="request_supplier"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("client_requests.id", ondelete="CASCADE"), nullable=False
    )
    counterparty_id: Mapped[int] = mapped_column(ForeignKey("counterparties.id"), nullable=False)
    to_addresses: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_intro: Mapped[str | None] = mapped_column(Text, nullable=True)


class Trip(AuditMixin, Base):
    __tablename__ = "trips"
    __table_args__ = (
        enum_check("status", TripStatus),
        enum_check("origin", TripOrigin),
        CheckConstraint("cancel_source IS NULL OR cancel_source IN ('email','user')", name="cancel_source"),
        CheckConstraint(
            "status = 'unplanned' OR sale_contract_id IS NOT NULL", name="sale_contract_unless_unplanned"
        ),
        CheckConstraint(
            "status IN ('unplanned','planned') "
            "OR (purchase_contract_id IS NOT NULL AND supplier_id IS NOT NULL)",
            name="purchase_contract_unless_unplanned",
        ),
        CheckConstraint(
            "status <> 'completed' OR (actual_qty_t IS NOT NULL AND load_date IS NOT NULL)",
            name="completed_needs_actual",
        ),
        Index("ix_trips_planned_date", "planned_date"),
        Index("ix_trips_status_planned_date", "status", "planned_date"),
        Index("ix_trips_sale_contract_id_status", "sale_contract_id", "status"),
        Index("ix_trips_purchase_contract_id_status", "purchase_contract_id", "status"),
        Index("ix_trips_plate_norm_planned_date", "plate_norm", "planned_date"),
        Index("ix_trips_customer_id_load_date", "customer_id", "load_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[int | None] = mapped_column(ForeignKey("client_requests.id"), nullable=True)
    sale_contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id"), nullable=True)
    purchase_contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id"), nullable=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("counterparties.id"), nullable=True)
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("counterparties.id"), nullable=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id"), nullable=True)
    planned_date: Mapped[date] = mapped_column(Date, nullable=False)
    plate_norm: Mapped[str] = mapped_column(Text, nullable=False)
    plate_display: Mapped[str] = mapped_column(Text, nullable=False)
    original_plate_norm: Mapped[str | None] = mapped_column(Text, nullable=True)
    driver_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    driver_phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    planned_qty_t: Mapped[Decimal] = mapped_column(Tonnes, nullable=False)
    actual_qty_t: Mapped[Decimal | None] = mapped_column(Tonnes, nullable=True)
    load_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    sale_price_per_t: Mapped[Decimal | None] = mapped_column(PricePerT, nullable=True)
    purchase_price_per_t: Mapped[Decimal | None] = mapped_column(PricePerT, nullable=True)
    supplier_report_line_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # FK (l2v.8)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    invoice_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # FK → invoices (l2v.10)


class ClientRequestLine(AuditMixin, Base):
    __tablename__ = "client_request_lines"
    __table_args__ = (
        UniqueConstraint("request_id", "line_no", name="request_line_no"),
        CheckConstraint("packaging IS NULL OR packaging IN ('bulk','bagged')", name="packaging"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("client_requests.id", ondelete="CASCADE"), nullable=False
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    planned_date: Mapped[date] = mapped_column(Date, nullable=False)
    plate_raw: Mapped[str] = mapped_column(Text, nullable=False)
    plate_norm: Mapped[str] = mapped_column(Text, nullable=False)
    trailer_plate: Mapped[str | None] = mapped_column(Text, nullable=True)
    driver_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    driver_phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    planned_qty_t: Mapped[Decimal] = mapped_column(Tonnes, nullable=False)
    uncertain_fields: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default=text("'{}'")
    )
    uncertain_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_removed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    trip_id: Mapped[int | None] = mapped_column(ForeignKey("trips.id"), nullable=True)
    packaging: Mapped[str | None] = mapped_column(Text, nullable=True)
    warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id"), nullable=True)
    purchase_contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id"), nullable=True)
    outbound_email_id: Mapped[int | None] = mapped_column(ForeignKey("outbound_emails.id"), nullable=True)
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)  # ред в оригиналния xlsx (1-based)
    truck_type: Mapped[str | None] = mapped_column(Text, nullable=True)  # ръчна промяна на типа в писмото
    own_transport: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    offer_note: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class TripEvent(CreatedMixin, Base):
    """История в детайла на курса — само добавяне."""

    __tablename__ = "trip_events"
    __table_args__ = (enum_check("kind", TripEventKind), Index("ix_trip_events_trip_id", "trip_id"))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id"), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    inbound_email_id: Mapped[int | None] = mapped_column(ForeignKey("inbound_emails.id"), nullable=True)
    outbound_email_id: Mapped[int | None] = mapped_column(ForeignKey("outbound_emails.id"), nullable=True)
