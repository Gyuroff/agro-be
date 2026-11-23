from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class ReportLineOut(BaseModel):
    load_date: date | None = None
    plate_raw: str = ""
    driver_name: str | None = None
    qty_t: Decimal | None = Field(default=None, gt=0, description="Тонове; null, ако в реда няма тонаж")
    source_row: int | None = Field(default=None, ge=1, description="N от префикса [rN]; null, ако няма")
    location_raw: str | None = Field(default=None, description="„Място на товарене“ както е написано")
    uncertain_fields: list[str] = Field(default_factory=list)  # plate | date | driver | qty
    uncertain_note: str | None = None


class ReportParserOutput(BaseModel):
    lines: list[ReportLineOut]
    notes: str | None = None
