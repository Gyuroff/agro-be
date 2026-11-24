"""Фактура в JSON (docs/api.md §8)."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.cards import Dec
from app.schemas.trips import TripListItemDTO


class RecipientDTO(BaseModel):
    name: str | None
    eik: str | None
    vat_number: str | None
    email: str | None


class InvoiceLineDTO(BaseModel):
    line_no: int
    description: str
    qty_t: Dec
    unit_price: Dec
    amount: Dec


class InvoiceTripsDTO(BaseModel):
    count: int
    qty_t: Dec
    items: list[TripListItemDTO]


class InvoiceStepDTO(BaseModel):
    key: Literal["issued", "emailed", "mega_export"]
    done: bool
    at: datetime | None = None
    by: str | None = None
    to: str | None = None
    error: str | None = None


class InvoiceDTO(BaseModel):
    id: int
    mode: str
    status: str
    number_display: str | None
    next_number_display: str | None
    issue_date: date | None
    tax_event_date: date | None
    due_date: date | None
    payment_terms_days: int | None
    recipient: RecipientDTO
    period_from: date
    period_to: date
    lines: list[InvoiceLineDTO]
    subtotal: Dec
    vat_rate: Dec
    vat_amount: Dec
    total: Dec
    trips: InvoiceTripsDTO
    steps: list[InvoiceStepDTO]
    version: int


class IssueInvoiceRequest(BaseModel):
    version: int


class VersionDTO(BaseModel):
    version: int
