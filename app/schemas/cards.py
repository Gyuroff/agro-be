"""Payload на картите по вид (docs/api.md §2 „payload по вид“).

`CardService` (br9.1) валидира `action_cards.payload` с `PAYLOAD_MODELS[kind]`. Пари и тонове са `Decimal`
и в JSON излизат като низове; непознати полета се отхвърлят (спецификацията е точният списък).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, PlainSerializer

from app.models.cards import CardKind

Dec = Annotated[Decimal, PlainSerializer(str, return_type=str, when_used="json")]


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClientRequestLineView(_Payload):
    date: date
    plate: str
    driver: str | None
    qty_t: Dec
    uncertain: bool


class ClientRequestPayload(_Payload):
    request_id: int
    inbound_email_id: int
    to: str
    subject: str
    greeting: str
    lines: list[ClientRequestLineView]
    total_trucks: int
    total_qty_t: Dec
    has_uncertain: bool


class ReportPreviewRow(_Payload):
    plate: str
    product: str
    qty_t: Dec


class ReportSubstitute(_Payload):
    unplanned_line_id: int
    missing_line_id: int
    customer: str
    date: date
    product: str
    new_plate: str
    new_qty_t: Dec
    warehouse_name: str | None
    old_plate: str
    old_planned_qty_t: Dec


class SupplierReportPayload(_Payload):
    report_id: int
    inbound_email_id: int
    received_at: datetime
    total_rows: int
    total_qty_t: Dec
    matched: int
    unplanned: int
    missing: int
    preview: list[ReportPreviewRow]
    substitutes: list[ReportSubstitute]
    customer_names: list[str]


class CancellationPayload(_Payload):
    trip_ids: list[int]
    date: date
    plates: list[str]
    supplier_name: str
    notify_supplier: bool


class InvoiceDueLine(_Payload):
    product: str
    qty_t: Dec
    unit_price: Dec
    amount: Dec


class InvoiceDuePayload(_Payload):
    invoice_id: int
    invoice_version: int
    period_from: date
    period_to: date
    schedule_label: str
    trip_count: int
    lines: list[InvoiceDueLine]
    vat_rate: Dec
    vat_amount: Dec
    total: Dec
    due_date: date


class ComparisonRow(_Payload):
    label: str
    erp: str
    mi: str


class InvoiceMismatchPayload(_Payload):
    comparison_id: int
    mi_number: str
    period_from: date
    period_to: date
    rows: list[ComparisonRow]
    difference: Dec
    explanation: str


class InvoiceMissingPayload(_Payload):
    invoice_id: int
    period_from: date
    period_to: date
    subtotal: Dec
    billing_date: date


class UnknownSenderPayload(_Payload):
    inbound_email_id: int
    from_address: str
    subject: str
    suggested_counterparty_id: int | None
    suggested_counterparty_name: str | None
    suggestion_reason: str | None
    auth_ok: bool


class ContractOverPayload(_Payload):
    contract_id: int
    contract_number: str
    product: str
    remaining_t: Dec
    requested_t: Dec
    shortage_t: Dec


class ExportErrorPayload(_Payload):
    invoice_id: int
    invoice_number: str
    error: str


class MailboxAlarmPayload(_Payload):
    last_ok_at: datetime | None
    minutes: int


class StorageUploadFailedPayload(_Payload):
    stored_object_id: int
    filename: str
    upload_attempts: int
    last_error: str | None


class InquiryPayload(_Payload):
    inbound_email_id: int
    from_address: str
    subject: str
    product_guess: str | None
    qty_t_guess: Dec | None
    suggested_counterparty_id: int | None


class PaymentQuestionPayload(_Payload):
    inbound_email_id: int
    counterparty_name: str
    invoice_number: str | None
    subject: str
    received_at: datetime


class EmailPreview(_Payload):
    to: str
    subject: str
    body: str


class OneShotSendPayload(_Payload):
    deal_id: int
    customer_name: str
    supplier_name: str
    product: str
    qty_t: Dec
    sell_price_per_t: Dec
    buy_price_per_t: Dec
    margin_per_t: Dec
    send_customer_proforma: bool
    send_supplier_request: bool
    customer_email_preview: EmailPreview
    supplier_email_preview: EmailPreview


class SupplierDocPayload(_Payload):
    deal_id: int
    inbound_email_id: int
    doc_kind: Literal["proforma", "invoice"]
    supplier_name: str
    qty_t: Dec
    price_per_t: Dec
    number: str


class OneShotPaySupplierPayload(_Payload):
    deal_id: int
    supplier_name: str
    amount: Dec
    supplier_doc_number: str


class SupplierAdvanceLowPayload(_Payload):
    purchase_contract_id: int
    contract_number: str
    supplier_name: str
    balance: Dec
    needed: Dec
    buffer: Dec
    shortfall: Dec
    trigger: Literal["request_send", "inbound_invoice", "daily"]


class StockLowPayload(_Payload):
    """Общият запас по активните покупни договори на стоката наближава сбора им (agro-2lg)."""

    product: str
    contracts: int
    total_t: Dec
    taken_t: Dec
    remaining_t: Dec
    remaining_pct: Dec


PAYLOAD_MODELS: dict[CardKind, type[_Payload]] = {
    CardKind.CLIENT_REQUEST: ClientRequestPayload,
    CardKind.SUPPLIER_REPORT: SupplierReportPayload,
    CardKind.CANCELLATION: CancellationPayload,
    CardKind.INVOICE_DUE: InvoiceDuePayload,
    CardKind.INVOICE_MISMATCH: InvoiceMismatchPayload,
    CardKind.INVOICE_MISSING: InvoiceMissingPayload,
    CardKind.UNKNOWN_SENDER: UnknownSenderPayload,
    CardKind.CONTRACT_OVER: ContractOverPayload,
    CardKind.EXPORT_ERROR: ExportErrorPayload,
    CardKind.MAILBOX_ALARM: MailboxAlarmPayload,
    CardKind.STORAGE_UPLOAD_FAILED: StorageUploadFailedPayload,
    CardKind.INQUIRY: InquiryPayload,
    CardKind.ONE_SHOT_SEND: OneShotSendPayload,
    CardKind.SUPPLIER_DOC: SupplierDocPayload,
    CardKind.ONE_SHOT_PAY_SUPPLIER: OneShotPaySupplierPayload,
    CardKind.SUPPLIER_ADVANCE_LOW: SupplierAdvanceLowPayload,
    CardKind.STOCK_LOW: StockLowPayload,
    CardKind.PAYMENT_QUESTION: PaymentQuestionPayload,
}
