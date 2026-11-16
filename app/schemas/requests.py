"""Заявка и редове в JSON (docs/api.md §3)."""

from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.cards import Dec
from app.schemas.trips import IdNumber

Tonnes = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=3)]


class RequestLineDTO(BaseModel):
    id: int
    line_no: int
    planned_date: date
    plate: str
    driver_name: str | None
    planned_qty_t: Dec
    uncertain_fields: list[str]
    uncertain_note: str | None


class RequestDTO(BaseModel):
    id: int
    status: str
    to: str | None
    supplier_name: str | None
    subject: str
    body_intro: str
    sale_contract: IdNumber | None
    lines: list[RequestLineDTO]
    warnings: list[str]
    version: int


class PatchRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    subject: str | None = Field(default=None, min_length=1)
    body_intro: str | None = None


class LineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    planned_date: date
    plate: str = Field(min_length=1)
    driver_name: str | None = None
    planned_qty_t: Tonnes
