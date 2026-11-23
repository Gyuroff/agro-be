"""Фактуриране: фактури, редове, курсове във фактура, Мега фактури, сравнения, опити за експорт
(docs/data-model.md §11)."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CHAR,
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

from app.db.base import AuditMixin, Base, CreatedMixin, Money, Pct, PricePerT, Tonnes, enum_check


class InvoiceMode(StrEnum):
    SHADOW = "shadow"
    ERP = "erp"


class InvoiceStatus(StrEnum):
    DRAFT = "draft"
    ISSUED = "issued"
    VOID = "void"


class MegaExportStatus(StrEnum):
    NOT_STARTED = "not_started"
    PENDING = "pending"
    OK = "ok"
    FAILED = "failed"


class MegaInvoiceSource(StrEnum):
    EMAIL_COPY = "email_copy"
    DELTA_DB = "delta_db"


class ComparisonResult(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    MISSING = "missing"


class ComparisonResolution(StrEnum):
    MEGA_CORRECT = "mega_correct"
    NOTIFY_ACCOUNTANT = "notify_accountant"


class MegaExportTarget(StrEnum):
    TEST = "test"
    PROD = "prod"


class MegaExportAttemptStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"


class Invoice(AuditMixin, Base):
    __tablename__ = "invoices"
    __table_args__ = (
        enum_check("mode", InvoiceMode),
        enum_check("status", InvoiceStatus),
        enum_check("mega_export_status", MegaExportStatus),
        UniqueConstraint("number_sequence_id", "number", name="uq_invoices_sequence_number"),
        # Няма две чернови/фактури за един и същи цикъл (void не се брои).
        Index(
            "uq_invoices_cycle",
            "counterparty_id",
            text("COALESCE(sale_contract_id, 0)"),
            "billing_date",
            unique=True,
            postgresql_where=text("status <> 'void'"),
        ),
        Index("ix_invoices_counterparty_id", "counterparty_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    counterparty_id: Mapped[int] = mapped_column(ForeignKey("counterparties.id"), nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default=InvoiceStatus.DRAFT.value, server_default=text("'draft'")
    )
    billing_date: Mapped[date] = mapped_column(Date, nullable=False)
    period_from: Mapped[date] = mapped_column(Date, nullable=False)
    period_to: Mapped[date] = mapped_column(Date, nullable=False)
    number_sequence_id: Mapped[int | None] = mapped_column(ForeignKey("number_sequences.id"), nullable=True)
    number: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    tax_event_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, default="EUR", server_default="EUR")
    subtotal: Mapped[Decimal] = mapped_column(Money, nullable=False)
    vat_amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    total: Mapped[Decimal] = mapped_column(Money, nullable=False)
    # Снимка на получателя: по закон фактурата не се мени при промяна на контрагента.
    recipient_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    recipient_eik: Mapped[str | None] = mapped_column(Text, nullable=True)
    recipient_vat_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    recipient_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    recipient_mol: Mapped[str | None] = mapped_column(Text, nullable=True)
    recipient_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Снимка на доставчика (нас).
    supplier_legal_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    supplier_eik: Mapped[str | None] = mapped_column(Text, nullable=True)
    supplier_vat_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    supplier_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    supplier_mol: Mapped[str | None] = mapped_column(Text, nullable=True)
    supplier_iban: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    issued_by_id: Mapped[int | None] = mapped_column(ForeignKey("actors.id"), nullable=True)
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outbound_email_id: Mapped[int | None] = mapped_column(ForeignKey("outbound_emails.id"), nullable=True)
    mega_export_status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=MegaExportStatus.NOT_STARTED.value,
        server_default=text("'not_started'"),
    )
    mega_exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sale_contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id"), nullable=True)


class InvoiceLine(AuditMixin, Base):
    """Ред = (договор, стока, цена): при смяна на цената в периода има два реда."""

    __tablename__ = "invoice_lines"
    __table_args__ = (UniqueConstraint("invoice_id", "line_no", name="uq_invoice_lines_invoice_line_no"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    sale_contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id"), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    qty_t: Mapped[Decimal] = mapped_column(Tonnes, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(PricePerT, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    vat_rate: Mapped[Decimal] = mapped_column(Pct, nullable=False)
    vat_amount: Mapped[Decimal] = mapped_column(Money, nullable=False)


class InvoiceTrip(CreatedMixin, Base):
    """Само за добавяне. Курсът е в най-много една издадена фактура (проверка в сервиза)."""

    __tablename__ = "invoice_trips"

    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), primary_key=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id"), primary_key=True)


class MegaInvoice(AuditMixin, Base):
    """Фактура, издадена от счетоводителя в Мега (за сравнение)."""

    __tablename__ = "mega_invoices"
    __table_args__ = (enum_check("source", MegaInvoiceSource),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    inbound_email_id: Mapped[int | None] = mapped_column(ForeignKey("inbound_emails.id"), nullable=True)
    attachment_id: Mapped[int | None] = mapped_column(ForeignKey("email_attachments.id"), nullable=True)
    number: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    counterparty_id: Mapped[int | None] = mapped_column(ForeignKey("counterparties.id"), nullable=True)
    recipient_eik: Mapped[str | None] = mapped_column(Text, nullable=True)
    lines: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    subtotal: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    vat_amount: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    total: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)


class InvoiceComparison(AuditMixin, Base):
    __tablename__ = "invoice_comparisons"
    __table_args__ = (
        enum_check("result", ComparisonResult),
        CheckConstraint(
            "resolution IS NULL OR resolution IN ('mega_correct','notify_accountant')", name="resolution"
        ),
        Index("ix_invoice_comparisons_invoice_id", "invoice_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), nullable=False)
    mega_invoice_id: Mapped[int | None] = mapped_column(ForeignKey("mega_invoices.id"), nullable=True)
    result: Mapped[str] = mapped_column(Text, nullable=False)
    diff: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    diff_kinds: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default=text("'{}'")
    )
    agent_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_id: Mapped[int | None] = mapped_column(ForeignKey("actors.id"), nullable=True)
    counts_for_readiness: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


class MegaExport(AuditMixin, Base):
    """Опит за запис в Мега. Форматът е неуточнен (отворени въпроси 10 и 12); `xml_path` е работно име."""

    __tablename__ = "mega_exports"
    __table_args__ = (
        enum_check("target", MegaExportTarget),
        enum_check("status", MegaExportAttemptStatus),
        Index("ix_mega_exports_invoice_id", "invoice_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), nullable=False)
    target: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    xml_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
