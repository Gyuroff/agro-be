from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentPrincipal, DbSession
from app.schemas.counterparties import CounterpartySearchItemDTO
from app.services.counterparty_search import search_counterparties

router = APIRouter(tags=["counterparties"])


@router.get("/counterparties/search", response_model=list[CounterpartySearchItemDTO])
def counterparties_search(
    _: CurrentPrincipal,
    session: DbSession,
    q: Annotated[str, Query(min_length=1)],
    card_id: int | None = None,
) -> list[CounterpartySearchItemDTO]:
    return search_counterparties(session, q, card_id)
