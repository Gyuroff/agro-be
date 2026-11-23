"""Карти „Изисква действие“, пуш журнал (docs/data-model.md §10, §13).

Колоните към още несъздадени таблици (`inbound_emails`, `outbound_emails`, `supplier_reports`,
`invoices`, `invoice_comparisons`, `one_shot_deals`) са `BIGINT` без FK — FK се добавя в миграцията на
съответната задача (l2v.6 / l2v.8 / l2v.10), както при `trips`.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditMixin, Base, enum_check


class CardKind(StrEnum):
    CLIENT_REQUEST = "client_request"
    SUPPLIER_REPORT = "supplier_report"
    CANCELLATION = "cancellation"
    INVOICE_DUE = "invoice_due"
    INVOICE_MISMATCH = "invoice_mismatch"
    INVOICE_MISSING = "invoice_missing"
    UNKNOWN_SENDER = "unknown_sender"
    CONTRACT_OVER = "contract_over"
    EXPORT_ERROR = "export_error"
    MAILBOX_ALARM = "mailbox_alarm"
    STORAGE_UPLOAD_FAILED = "storage_upload_failed"
    INQUIRY = "inquiry"
    ONE_SHOT_SEND = "one_shot_send"
    SUPPLIER_DOC = "supplier_doc"
    ONE_SHOT_PAY_SUPPLIER = "one_shot_pay_supplier"
    SUPPLIER_ADVANCE_LOW = "supplier_advance_low"
    STOCK_LOW = "stock_low"
    PAYMENT_QUESTION = "payment_question"


class CardStatus(StrEnum):
    OPEN = "open"
    DONE = "done"
    DISMISSED = "dismissed"
    SUPERSEDED = "superseded"


class CardResolution(StrEnum):
    SENT = "sent"
    REJECTED = "rejected"
    CONFIRMED = "confirmed"
    KEPT = "kept"
    LINKED = "linked"
    IGNORED = "ignored"
    HIDDEN = "hidden"
    ISSUED = "issued"
    MI_CORRECT = "mi_correct"
    NOTIFY_ACCOUNTANT = "notify_accountant"
    RETRIED = "retried"


class AgentName(StrEnum):
    EMAIL_CLASSIFIER = "email_classifier"
    REQUEST_PARSER = "request_parser"
    REPORT_PARSER = "report_parser"
    REPORT_MATCHER = "report_matcher"
    CANCELLATION_PARSER = "cancellation_parser"
    MI_INVOICE_PARSER = "mi_invoice_parser"
    INVOICE_COMPARATOR = "invoice_comparator"


class AgentRunStatus(StrEnum):
    RUNNING = "running"
    OK = "ok"
    ERROR = "error"


class PushKind(StrEnum):
    """Видовете на картите + `send_failed` (P4) и `digest` (P12) — docs/push.md."""

    CLIENT_REQUEST = CardKind.CLIENT_REQUEST.value
    SUPPLIER_REPORT = CardKind.SUPPLIER_REPORT.value
    CANCELLATION = CardKind.CANCELLATION.value
    INVOICE_DUE = CardKind.INVOICE_DUE.value
    INVOICE_MISMATCH = CardKind.INVOICE_MISMATCH.value
    INVOICE_MISSING = CardKind.INVOICE_MISSING.value
    UNKNOWN_SENDER = CardKind.UNKNOWN_SENDER.value
    CONTRACT_OVER = CardKind.CONTRACT_OVER.value
    EXPORT_ERROR = CardKind.EXPORT_ERROR.value
    MAILBOX_ALARM = CardKind.MAILBOX_ALARM.value
    STORAGE_UPLOAD_FAILED = CardKind.STORAGE_UPLOAD_FAILED.value
    INQUIRY = CardKind.INQUIRY.value
    ONE_SHOT_SEND = CardKind.ONE_SHOT_SEND.value
    SUPPLIER_DOC = CardKind.SUPPLIER_DOC.value
    ONE_SHOT_PAY_SUPPLIER = CardKind.ONE_SHOT_PAY_SUPPLIER.value
    SUPPLIER_ADVANCE_LOW = CardKind.SUPPLIER_ADVANCE_LOW.value
    STOCK_LOW = CardKind.STOCK_LOW.value
    SEND_FAILED = "send_failed"
    DIGEST = "digest"


class PushProvider(StrEnum):
    FCM = "fcm"
    HMS = "hms"
    EMAIL = "email"


class PushStatus(StrEnum):
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"  # картата е затворена преди изпращане


class ActionCard(AuditMixin, Base):
    __tablename__ = "action_cards"
    __table_args__ = (
        enum_check("kind", CardKind),
        enum_check("status", CardStatus),
        # CHECK (resolution IS NULL OR resolution IN (...)) — NULL минава през IN, затова enum_check стига.
        enum_check("resolution", CardResolution),
        Index("ix_action_cards_status_created_at", "status", text("created_at DESC")),
        Index(
            "uq_action_cards_dedupe_key_open",
            "dedupe_key",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    one_shot_deal_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # FK (one_shot_deals)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default=CardStatus.OPEN.value, server_default=CardStatus.OPEN.value
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    subtitle: Mapped[str | None] = mapped_column(Text, nullable=True)
    counterparty_id: Mapped[int | None] = mapped_column(ForeignKey("counterparties.id"), nullable=True)
    call_phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    inbound_email_id: Mapped[int | None] = mapped_column(ForeignKey("inbound_emails.id"), nullable=True)
    outbound_email_id: Mapped[int | None] = mapped_column(ForeignKey("outbound_emails.id"), nullable=True)
    client_request_id: Mapped[int | None] = mapped_column(ForeignKey("client_requests.id"), nullable=True)
    supplier_report_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # FK (l2v.8)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id"), nullable=True)
    invoice_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # FK (l2v.10)
    invoice_comparison_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # FK (l2v.10)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    dedupe_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_id: Mapped[int | None] = mapped_column(ForeignKey("actors.id"), nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    push_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentRun(AuditMixin, Base):
    """Изпълнение на агент — без ред в `audit_log` (AUDIT_EXCLUDED_TABLES)."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        enum_check("agent", AgentName),
        enum_check("status", AgentRunStatus),
        Index("ix_agent_runs_subject", "subject_type", "subject_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    agent: Mapped[str] = mapped_column(Text, nullable=False)
    subject_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    thread_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class PushNotification(AuditMixin, Base):
    """Пуш журнал — без ред в `audit_log` (AUDIT_EXCLUDED_TABLES), правила в docs/push.md."""

    __tablename__ = "push_notifications"
    __table_args__ = (
        enum_check("kind", PushKind),
        enum_check("provider", PushProvider),
        enum_check("status", PushStatus),
        Index("ix_push_notifications_status_scheduled_for", "status", "scheduled_for"),
        Index("ix_push_notifications_action_card_id", "action_card_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    is_urgent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), nullable=False)
    action_card_id: Mapped[int | None] = mapped_column(ForeignKey("action_cards.id"), nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default=PushStatus.QUEUED.value, server_default=PushStatus.QUEUED.value
    )
    provider_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
