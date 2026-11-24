from typing import Annotated, Any

from fastapi import APIRouter, Body

from app.api.deps import CurrentPrincipal, DbSession
from app.models.cards import CardStatus
from app.schemas.dashboard import CardActionResponse, DashboardDTO
from app.schemas.proposals import ProposalDTO
from app.services import cancellations as _cancellations  # noqa: F401  (регистрира действията)
from app.services import invoice_service as _invoice_service  # noqa: F401  (регистрира действията)
from app.services import payment_questions as _payment_questions  # noqa: F401  (регистрира действията)
from app.services import unknown_sender as _unknown_sender  # noqa: F401  (регистрира действията)
from app.services.cards import CardService
from app.services.dashboard import build_dashboard, card_dto

router = APIRouter(tags=["cards"])


@router.get("/dashboard", response_model=DashboardDTO)
def dashboard(_: CurrentPrincipal, session: DbSession) -> DashboardDTO:
    return build_dashboard(session)


@router.post("/cards/{card_id}/actions/{action}", response_model=CardActionResponse)
def card_action(
    card_id: int,
    action: str,
    _: CurrentPrincipal,
    session: DbSession,
    body: Annotated[dict[str, Any] | None, Body()] = None,
) -> CardActionResponse:
    """`card` е `null`, когато действието е затворило картата (тя вече не е на таблото)."""
    card, result = CardService.perform_action(session, card_id, action, body or {})
    still_open = card.status == CardStatus.OPEN.value
    return CardActionResponse(
        card=card_dto(session, card) if still_open else None,
        snackbar=result.snackbar,
        undo_token=result.undo_token,
        proposal=ProposalDTO.model_validate(result.proposal) if result.proposal is not None else None,
    )
