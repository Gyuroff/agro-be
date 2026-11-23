from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentPrincipal, DbSession
from app.schemas.result import ResultDTO
from app.services.result import ResultService

router = APIRouter(tags=["result"])


@router.get("/result", response_model=ResultDTO)
def result_get(
    _: CurrentPrincipal,
    session: DbSession,
    month: Annotated[str | None, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")] = None,
) -> ResultDTO:
    parsed = date(int(month[:4]), int(month[5:]), 1) if month else None
    return ResultService(session).build(parsed)
