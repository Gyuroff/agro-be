from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.api.deps import CurrentPrincipal, DbSession
from app.schemas.contracts import ContractDetailDTO, ContractListDTO, ContractSearchItemDTO
from app.services.contract_views import contract_detail, get_contract, list_contracts, search_contracts

router = APIRouter(tags=["contracts"])

Side = Literal["sale", "purchase"]


@router.get("/contracts", response_model=ContractListDTO)
def contracts_list(_: CurrentPrincipal, session: DbSession, side: Side | None = None) -> ContractListDTO:
    return list_contracts(session, side)


# Преди /contracts/{contract_id}, иначе „search“ се чете като id.
@router.get("/contracts/search", response_model=list[ContractSearchItemDTO])
def contracts_search(
    _: CurrentPrincipal,
    session: DbSession,
    q: Annotated[str, Query(min_length=1)],
    side: Side | None = None,
    active: bool = True,
) -> list[ContractSearchItemDTO]:
    return search_contracts(session, q, side, active)


@router.get("/contracts/{contract_id}", response_model=ContractDetailDTO)
def contract_get(contract_id: int, _: CurrentPrincipal, session: DbSession) -> ContractDetailDTO:
    return contract_detail(session, get_contract(session, contract_id))
