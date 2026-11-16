"""Курсове в JSON (docs/api.md §5)."""

from datetime import date
from typing import Annotated

from pydantic import BaseModel, PlainSerializer

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
