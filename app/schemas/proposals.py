"""DTO на предложенията от съветния режим (docs/api.md, „Съветен режим“)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ProposalDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    title: str
    payload: dict[str, Any]
    status: str
    created_at: datetime
