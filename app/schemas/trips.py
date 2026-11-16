"""Курсове в JSON (docs/api.md §5)."""

from datetime import UTC, date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, PlainSerializer

from app.schemas.cards import Dec

NullableDec = Annotated[
    Dec | None, PlainSerializer(lambda v: None if v is None else str(v), when_used="json")
]


class TripListItemDTO(BaseModel):
    id: int
    plate: str
    product: str
    route: str
    date: date
    status: str
    actual_qty_t: NullableDec
    planned_qty_t: Dec


def _utc_z(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


UtcTime = Annotated[datetime, PlainSerializer(_utc_z, return_type=str, when_used="json")]


class TripGroupDTO(BaseModel):
    date: date
    count: int
    qty_t: Dec
    qty_kind: Literal["actual", "planned"]
    items: list[TripListItemDTO]


class TripListDTO(BaseModel):
    groups: list[TripGroupDTO]


class IdName(BaseModel):
    id: int
    name: str


class IdNumber(BaseModel):
    id: int
    number: str


class TripEventDTO(BaseModel):
    at: UtcTime
    text: str


class TripDetailDTO(TripListItemDTO):
    customer: IdName | None
    sale_contract: IdNumber | None
    supplier: IdName
    warehouse: str | None
    driver_name: str | None
    driver_phone: str | None
    load_date: date | None
    events: list[TripEventDTO]
    allowed_actions: list[Literal["cancel", "link_contract"]]
    version: int


class CancelTripRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    reason: str | None = None


class LinkContractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sale_contract_id: int
    version: int
