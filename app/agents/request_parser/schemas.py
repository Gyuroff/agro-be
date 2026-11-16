from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

UncertainField = str  # 'plate' | 'date' | 'driver' | 'qty' | 'trailer' | 'driver_phone'


class ParsedLine(BaseModel):
    date: date
    plate_raw: str = ""  # празен, ако в заявката няма номер
    trailer_plate: str | None = None
    driver_name: str | None = None
    driver_phone: str | None = None
    qty_t: Decimal | None = Field(default=None, gt=0, description="Тонове; null, ако не е посочено")
    uncertain_fields: list[UncertainField] = Field(default_factory=list)
    uncertain_note: str | None = None


class RequestParserOutput(BaseModel):
    period_from: date | None = None
    period_to: date | None = None
    lines: list[ParsedLine]
    product_hint: str | None = None
    warehouse_hint: str | None = None
    notes: str | None = None
