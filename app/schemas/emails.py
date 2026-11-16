"""Оригинален имейл (docs/api.md §3, екран 5)."""

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
