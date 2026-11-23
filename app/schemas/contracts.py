"""Договори в JSON (docs/api.md §5–§6)."""

from datetime import date
from typing import Literal

from pydantic import BaseModel

from app.schemas.cards import Dec
from app.schemas.trips import NullableDec, TripListItemDTO


class ContractRowDTO(BaseModel):
    id: int
    number: str
    counterparty_name: str
    warehouse_name: str | None
    valid_to: date | None
    product: str
    contracted_t: NullableDec
    drawn_t: Dec
    requested_t: Dec
    remaining_t: NullableDec
    state: Literal["ok", "over", "open"]
    funds_balance: NullableDec
    funds_held_by: Literal["counterparty", "us"] | None
    funds_label: str | None


class ContractListDTO(BaseModel):
    active_total: int
    sale_count: int
    purchase_count: int
    items: list[ContractRowDTO]


class PurchasePriceDTO(BaseModel):
    packaging: Literal["bulk", "bagged"]
    price_per_t: Dec


class TripsSummaryDTO(BaseModel):
    count: int
    qty_t: Dec


class ContractDetailDTO(ContractRowDTO):
    side: Literal["sale", "purchase"]
    valid_from: date
    sale_price_per_t: NullableDec
    purchase_price_per_t: NullableDec
    purchase_prices: list[PurchasePriceDTO]
    purchase_counterparty_name: str | None
    planned_truck_qty_t: Dec
    billing_label: str | None
    next_billing_date: date | None
    payment_terms_days: int | None
    purchase_payment_mode: Literal["prepaid", "pay_on_invoice", "pay_after_client"] | None
    # TODO(agro-w5h): блокът funds (balance/reserved/available/needed/buffer/shortfall) — виж bd note.
    trips_summary: TripsSummaryDTO
    recent_trips: list[TripListItemDTO]


class ContractSearchItemDTO(BaseModel):
    id: int
    number: str
    counterparty_name: str
    product: str
    remaining_t: NullableDec
