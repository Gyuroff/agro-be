"""Заявки: GET, PATCH на писмото по доставчик, редове, undo (docs/api.md §2–§3).

„Изпрати“/„Отхвърли“ са действия по картата.
"""

from fastapi import APIRouter, Response

from app.api.deps import CurrentPrincipal, DbSession
from app.schemas.requests import LineBody, OfferDTO, PatchEmailBody, PreviewDTO, RequestDTO
from app.services.client_confirmation import ClientConfirmationService
from app.services.requests import RequestService

router = APIRouter(tags=["requests"])


@router.get("/requests/{request_id}", response_model=RequestDTO)
def request_get(request_id: int, _: CurrentPrincipal, session: DbSession) -> RequestDTO:
    return RequestService.to_dto(session, RequestService.get(session, request_id))


@router.get("/requests/{request_id}/preview", response_model=PreviewDTO)
def request_preview(request_id: int, _: CurrentPrincipal, session: DbSession) -> PreviewDTO:
    return RequestService.preview(session, RequestService.get(session, request_id))


@router.get("/requests/{request_id}/lines/{line_id}/offers", response_model=list[OfferDTO])
def line_offers(request_id: int, line_id: int, _: CurrentPrincipal, session: DbSession) -> list[OfferDTO]:
    return RequestService.line_offers(session, RequestService.get(session, request_id), line_id)


@router.patch("/requests/{request_id}/emails/{supplier_id}", response_model=RequestDTO)
def email_patch(
    request_id: int, supplier_id: int, body: PatchEmailBody, _: CurrentPrincipal, session: DbSession
) -> RequestDTO:
    request = RequestService.get(session, request_id)
    RequestService.update_email(session, request, supplier_id, body)
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


@router.get("/requests/{request_id}/client-confirmation")
def confirmation_preview(request_id: int, _: CurrentPrincipal, session: DbSession) -> dict[str, object]:
    return ClientConfirmationService.preview(session, RequestService.get(session, request_id))


@router.post("/requests/{request_id}/client-confirmation/send", status_code=202)
def confirmation_send(request_id: int, _: CurrentPrincipal, session: DbSession) -> dict[str, int]:
    outbound = ClientConfirmationService.send(session, RequestService.get(session, request_id))
    return {"outbound_email_id": outbound.id}


@router.post("/undo/{undo_token}", status_code=200)
def undo(undo_token: str, _: CurrentPrincipal, session: DbSession) -> Response:
    RequestService.undo(session, undo_token)
    return Response(status_code=200)
