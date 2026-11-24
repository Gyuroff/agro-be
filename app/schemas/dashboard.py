"""Табло и карти в JSON (docs/api.md §2)."""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel

from app.schemas.cards import Dec
from app.schemas.emails import ReplyDTO
from app.schemas.proposals import ProposalDTO
from app.schemas.trips import TripListItemDTO


class CardDTO(BaseModel):
    id: int
    kind: str
    title: str
    subtitle: str | None
    created_at: datetime
    call_phone: str | None
    payload: dict[str, Any]
    reply: ReplyDTO | None = None


class TripsTodayDTO(BaseModel):
    count: int
    planned_qty_t: Dec
    items: list[TripListItemDTO]


class DashboardDTO(BaseModel):
    today: date
    last_mail_check_at: datetime | None
    cards: list[CardDTO]
    trips_today: TripsTodayDTO


class CardActionResponse(BaseModel):
    card: CardDTO | None
    snackbar: str | None
    undo_token: str | None
    proposal: ProposalDTO | None = None
