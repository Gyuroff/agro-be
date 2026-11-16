from enum import StrEnum

from pydantic import BaseModel, Field


class EmailKind(StrEnum):
    REQUEST = "request"
    REPORT = "report"
    CANCELLATION = "cancellation"
    MI_INVOICE = "mi_invoice"
    OTHER = "other"


class ClassifierOutput(BaseModel):
    kind: EmailKind
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, description="Едно изречение на български")
