"""DTO на предложенията от съветния режим (docs/api.md, „Съветен режим“)."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class ProposalDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    title: str
    payload: dict[str, Any]
    status: str
    created_at: datetime


class ProposalFeedbackDTO(BaseModel):
    verdict: Literal["correct", "wrong"]
    note_text: str | None


class ProposalDetailDTO(ProposalDTO):
    """`ProposalDTO` + вердикта (GET /proposals/{id}, отговор на POST …/feedback)."""

    feedback: ProposalFeedbackDTO | None


class ProposalFeedbackRequest(BaseModel):
    verdict: Literal["correct", "wrong"]
    note_text: str | None = None
