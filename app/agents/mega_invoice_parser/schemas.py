import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, Field


class MegaInvoiceLine(BaseModel):
    description: str
    qty_t: Decimal = Field(description="Количество в тонове")
    unit_price: Decimal
    amount: Decimal = Field(description="Стойност на реда без ДДС")
    vat_rate: Decimal = Field(description="ДДС в проценти, напр. 20")


class MegaInvoiceParserOutput(BaseModel):
    number: str | None = Field(default=None, description="Номер на фактурата, напр. 0000001524")
    issue_date: dt.date | None = None
    recipient_eik: str | None = Field(default=None, description="ЕИК на ПОЛУЧАТЕЛЯ (клиента), само цифри")
    lines: list[MegaInvoiceLine] = Field(default_factory=list)
    subtotal: Decimal | None = Field(default=None, description="Данъчна основа")
    vat_amount: Decimal | None = None
    total: Decimal | None = None
