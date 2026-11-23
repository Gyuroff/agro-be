from pydantic import BaseModel, Field

from app.models.mail import DocKind, EmailKind, ReplyStatus

__all__ = ["ClassifierOutput", "DocKind", "EmailKind", "ReplyStatus"]


class ClassifierOutput(BaseModel):
    kind: EmailKind
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, description="Едно изречение на български")
    # само при kind=supplier_doc (docs/llm.md §1а)
    doc_kind: DocKind | None = None
    # само при kind=reply
    reply_status: ReplyStatus | None = None
