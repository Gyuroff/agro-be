"""Заявка и редове в JSON (docs/api.md §3)."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.cards import Dec
from app.schemas.trips import IdNumber

Tonnes = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=3)]


class WarehouseRef(BaseModel):
    id: int
    name: str
    supplier_name: str


class OfferDTO(BaseModel):
    warehouse: WarehouseRef
    contract_id: int
    price_per_t: Dec
    is_cheapest: bool


class RequestLineDTO(BaseModel):
    id: int
    line_no: int
    planned_date: date
    plate: str
    driver_name: str | None
    planned_qty_t: Dec
    uncertain_fields: list[str]
    uncertain_note: str | None
    packaging: str | None = None
    warehouse: "WarehouseRef | None" = None
    purchase_contract_id: int | None = None
    own_transport: bool = False
    truck_type: str | None = None
    needs_choice: bool = False  # „избери“: няма склад/доставчик — Ива решава
    offer_note: dict[str, Any] | None = None


class EmailPreviewDTO(BaseModel):
    """Едно реално писмо до доставчик."""

    supplier_name: str
    to: str
    subject: str
    body_text: str


class PreviewDTO(BaseModel):
    """`GET /requests/{id}/preview`: по едно писмо на доставчик."""

    emails: list[EmailPreviewDTO]


class GroupEmailDTO(BaseModel):
    to: list[str]
    subject: str
    body_intro: str
    version: int  # версията НА ПИСМОТО (client_request_supplier_drafts.version; 1, докато няма ред)


class SupplierGroupDTO(BaseModel):
    """`supplier_id=None` е „Без склад“: без писмо."""

    supplier_id: int | None
    supplier_name: str | None
    line_ids: list[int]
    email: GroupEmailDTO | None


class RequestDTO(BaseModel):
    id: int
    status: str
    customer_name: str
    sale_contract: IdNumber | None
    lines: list[RequestLineDTO]
    groups: list[SupplierGroupDTO]
    warnings: list[str]
    version: int


class PatchEmailBody(BaseModel):
    """`PATCH /requests/{id}/emails/{supplier_id}`: явен `null` връща подразбиращото се."""

    model_config = ConfigDict(extra="forbid")
    version: int
    to: list[str] | None = None
    subject: str | None = Field(default=None, min_length=1)
    body_intro: str | None = None


class LineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    planned_date: date
    plate: str = ""  # празен само при own_transport („транспорт Ива“)
    driver_name: str | None = None
    planned_qty_t: Tonnes
    packaging: Literal["bulk", "bagged"] | None = None
    warehouse_id: int | None = None
    own_transport: bool = False
    truck_type: str | None = None  # ръчен тип камион в писмото; празно = по подразбиране
