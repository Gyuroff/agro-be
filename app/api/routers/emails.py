from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.api.deps import CurrentPrincipal, DbSession
from app.schemas.emails import EmailDTO
from app.services.email_views import get_attachment, get_email, get_outbound_email

router = APIRouter(tags=["emails"])


@router.get("/emails/{email_id}", response_model=EmailDTO)
def email_get(email_id: int, _: CurrentPrincipal, session: DbSession) -> EmailDTO:
    return get_email(session, email_id)


@router.get("/outbound-emails/{email_id}", response_model=EmailDTO)
def outbound_email_get(email_id: int, _: CurrentPrincipal, session: DbSession) -> EmailDTO:
    """Изходящо писмо с етикет „отговор“ (отделен маршрут от `/emails/{id}`: id-тата се застъпват)."""
    return get_outbound_email(session, email_id)


@router.get("/attachments/{attachment_id}")
def attachment_get(attachment_id: int, _: CurrentPrincipal, session: DbSession) -> FileResponse:
    """Файлът като `Content-Disposition: attachment` (docs/api.md §3)."""
    attachment = get_attachment(session, attachment_id)
    return FileResponse(
        attachment.storage_path,
        media_type=attachment.content_type,
        filename=attachment.filename,
        content_disposition_type="attachment",
    )
