from fastapi import APIRouter

from app.api.deps import CurrentPrincipal, DbSession
from app.models.invoicing import ComparisonResolution
from app.schemas.invoices import ComparisonDTO, ResolveComparisonRequest
from app.services.mega_comparison import comparison_dto, get_comparison, resolve_comparison

router = APIRouter(tags=["comparisons"])


@router.get("/comparisons/{comparison_id}", response_model=ComparisonDTO)
def comparison_get(comparison_id: int, _: CurrentPrincipal, session: DbSession) -> ComparisonDTO:
    return comparison_dto(session, get_comparison(session, comparison_id))


@router.post("/comparisons/{comparison_id}/resolve", response_model=ComparisonDTO)
def comparison_resolve(
    comparison_id: int, body: ResolveComparisonRequest, _: CurrentPrincipal, session: DbSession
) -> ComparisonDTO:
    comparison = resolve_comparison(session, comparison_id, ComparisonResolution(body.resolution))
    return comparison_dto(session, comparison)
