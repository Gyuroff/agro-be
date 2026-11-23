"""Поща (docs/data-model.md §7): входящи, прикачени, изходящи, проверки на пощата."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditMixin, Base, CreatedMixin, enum_check


class MatchMethod(StrEnum):
    THREAD = "thread"
    SUBJECT_CODE = "subject_code"
    CONTACT = "contact"
    DOMAIN = "domain"
    MANUAL = "manual"


class EmailKind(StrEnum):
    REQUEST = "request"
    REPORT = "report"
    CANCELLATION = "cancellation"
    MEGA_INVOICE = "mega_invoice"
    SUPPLIER_DOC = "supplier_doc"
    INQUIRY = "inquiry"
    PAYMENT_QUESTION = "payment_question"
    REPLY = "reply"
    OTHER = "other"


class DocKind(StrEnum):
    PROFORMA = "proforma"
    INVOICE = "invoice"


class ReplyStatus(StrEnum):
    CONFIRMED = "confirmed"
    NEEDS_ATTENTION = "needs_attention"


class InboundStatus(StrEnum):
    NEW = "new"
    PROCESSING = "processing"
    PROCESSED = "processed"
    NEEDS_ACTION = "needs_action"
    IGNORED = "ignored"
    FAILED = "failed"


class OutboundPurpose(StrEnum):
    SUPPLIER_ORDER = "supplier_order"
    SUPPLIER_CANCELLATION = "supplier_cancellation"
    INVOICE = "invoice"
    ACCOUNTANT_NOTE = "accountant_note"
    PAYMENT_REPLY = "payment_reply"
    CLIENT_CONFIRMATION = "client_confirmation"


class OutboundStatus(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"
    DISCARDED = "discarded"


class PollStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


class InboundEmail(AuditMixin, Base):
    __tablename__ = "inbound_emails"
    __table_args__ = (
        enum_check("match_method", MatchMethod),
        enum_check("kind", EmailKind),
        enum_check("doc_kind", DocKind),
        enum_check("reply_status", ReplyStatus),
        enum_check("status", InboundStatus),
        # Колоните са nullable: NULL минава през CHECK (трипозиционна логика).
        CheckConstraint("kind_confidence BETWEEN 0 AND 1", name="kind_confidence"),
        Index("ix_inbound_emails_status", "status"),
        Index("ix_inbound_emails_counterparty_id_received_at", "counterparty_id", text("received_at DESC")),
        Index("ix_inbound_emails_reference_code", "reference_code"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    imap_uid: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    in_reply_to: Mapped[str | None] = mapped_column(Text, nullable=True)
    references: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    from_address: Mapped[str] = mapped_column(Text, nullable=False)
    from_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_addresses: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default=text("'{}'")
    )
    cc_addresses: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default=text("'{}'")
    )
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_path: Mapped[str] = mapped_column(Text, nullable=False)
    auth_spf: Mapped[str | None] = mapped_column(Text, nullable=True)
    auth_dkim: Mapped[str | None] = mapped_column(Text, nullable=True)
    auth_dmarc: Mapped[str | None] = mapped_column(Text, nullable=True)
    auth_ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reference_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    counterparty_id: Mapped[int | None] = mapped_column(ForeignKey("counterparties.id"), nullable=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"), nullable=True)
    match_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    doc_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    replies_to_outbound_id: Mapped[int | None] = mapped_column(
        ForeignKey("outbound_emails.id", use_alter=True), nullable=True
    )
    reply_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default=InboundStatus.NEW.value, server_default=InboundStatus.NEW.value
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OutboundEmail(AuditMixin, Base):
    __tablename__ = "outbound_emails"
    __table_args__ = (enum_check("purpose", OutboundPurpose), enum_check("status", OutboundStatus))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reference_code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    counterparty_id: Mapped[int | None] = mapped_column(ForeignKey("counterparties.id"), nullable=True)
    to_addresses: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    cc_addresses: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list, server_default=text("'{}'")
    )
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    in_reply_to_email_id: Mapped[int | None] = mapped_column(ForeignKey("inbound_emails.id"), nullable=True)
    message_id: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default=OutboundStatus.DRAFT.value, server_default=OutboundStatus.DRAFT.value
    )
    send_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("actors.id"), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class EmailAttachment(AuditMixin, Base):
    __tablename__ = "email_attachments"
    __table_args__ = (
        CheckConstraint(
            "(inbound_email_id IS NOT NULL) <> (outbound_email_id IS NOT NULL)", name="exactly_one_email"
        ),
        Index("ix_email_attachments_inbound_email_id", "inbound_email_id"),
        Index("ix_email_attachments_outbound_email_id", "outbound_email_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    inbound_email_id: Mapped[int | None] = mapped_column(ForeignKey("inbound_emails.id"), nullable=True)
    outbound_email_id: Mapped[int | None] = mapped_column(ForeignKey("outbound_emails.id"), nullable=True)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)


class MailboxPoll(CreatedMixin, Base):
    """Само добавяне, без ред в `audit_log` (AUDIT_EXCLUDED_TABLES)."""

    __tablename__ = "mailbox_polls"
    __table_args__ = (enum_check("status", PollStatus),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
