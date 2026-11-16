"""Заявки: GET/PATCH, редове, undo (docs/api.md §2–§3). „Изпрати“/„Отхвърли“ са действия по картата."""

from fastapi import APIRouter, Response

from app.api.deps import CurrentPrincipal, DbSession
from app.schemas.requests import LineBody, PatchRequestBody, RequestDTO
from app.services.requests import RequestService

router = APIRouter(tags=["requests"])


@router.get("/requests/{request_id}", response_model=RequestDTO)
def request_get(request_id: int, _: CurrentPrincipal, session: DbSession) -> RequestDTO:
    return RequestService.to_dto(session, RequestService.get(session, request_id))


@router.patch("/requests/{request_id}", response_model=RequestDTO)
def request_patch(
    request_id: int, body: PatchRequestBody, _: CurrentPrincipal, session: DbSession
) -> RequestDTO:
    request = RequestService.update(session, RequestService.get(session, request_id), body)
    return RequestService.to_dto(session, request)


@router.put("/requests/{request_id}/lines/{line_id}", response_model=RequestDTO)
def line_put(
    request_id: int, line_id: int, body: LineBody, _: CurrentPrincipal, session: DbSession
) -> RequestDTO:
    request = RequestService.get(session, request_id)
    RequestService.update_line(session, request, line_id, body)
    return RequestService.to_dto(session, request)


@router.post("/requests/{request_id}/lines", response_model=RequestDTO, status_code=201)
def line_add(request_id: int, body: LineBody, _: CurrentPrincipal, session: DbSession) -> RequestDTO:
    request = RequestService.get(session, request_id)
    RequestService.add_line(session, request, body)
    return RequestService.to_dto(session, request)


@router.delete("/requests/{request_id}/lines/{line_id}", response_model=RequestDTO)
def line_delete(request_id: int, line_id: int, _: CurrentPrincipal, session: DbSession) -> RequestDTO:
    request = RequestService.get(session, request_id)
    RequestService.remove_line(session, request, line_id)
    return RequestService.to_dto(session, request)


@router.post("/undo/{undo_token}", status_code=200)
def undo(undo_token: str, _: CurrentPrincipal, session: DbSession) -> Response:
    RequestService.undo(session, undo_token)
    return Response(status_code=200)
