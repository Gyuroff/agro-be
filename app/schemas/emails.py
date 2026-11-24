"""Оригинален имейл (docs/api.md §3, екран 5)."""

from typing import Literal

from pydantic import BaseModel

from app.schemas.trips import IdName, UtcTime


class EmailAttachmentDTO(BaseModel):
    id: int
    filename: str
    content_type: str
    size_bytes: int
    rows: int | None


class AgentNoteDTO(BaseModel):
    label: str
    value: str
    why: str | None = None


class UncertainDTO(BaseModel):
    label: str
    why: str | None


class ReplyDTO(BaseModel):
    """Етикет „отговор“ (docs/api.md §3): какво е станало с писмото, което сме изпратили."""

    status: Literal["awaiting", "confirmed", "needs_attention"]
    counterparty_short_name: str | None
    sent_at: UtcTime | None
    received_at: UtcTime | None
    inbound_email_id: int | None
    count: int


class EmailDTO(BaseModel):
    from_name: str | None
    from_address: str
    to: list[str]
    received_at: UtcTime
    subject: str | None
    body_text: str | None
    verified_sender: bool
    counterparty: IdName | None
    match_label: str | None
    call_phone: str | None
    attachments: list[EmailAttachmentDTO]
    agent_notes: list[AgentNoteDTO]
    uncertain: list[UncertainDTO]
    reply: ReplyDTO | None = None
