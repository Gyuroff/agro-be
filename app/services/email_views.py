"""Четене на входящо писмо за екран 5 (docs/api.md §3, agro-br9.9 / agro-sw1)."""

from pathlib import Path

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.models.mail import EmailAttachment, InboundEmail, MatchMethod
from app.models.masterdata import Contact, Counterparty
from app.models.trips import ClientRequest, ClientRequestLine
from app.schemas.emails import (
    AgentNoteDTO,
    EmailAttachmentDTO,
    EmailDTO,
    UncertainDTO,
)
from app.schemas.trips import IdName

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# Как е съпоставен подателят (суфиксът на „Агро Трейд ООД · контакт“). Текстовете са по data-model §7.
MATCH_LABELS = {
    MatchMethod.THREAD: "нишка",
    MatchMethod.SUBJECT_CODE: "код в темата",
    MatchMethod.CONTACT: "контакт",
    MatchMethod.DOMAIN: "домейн",
    MatchMethod.MANUAL: "ръчно",
}

FIELD_LABELS = {
    "plate": "Номер",
    "date": "Дата",
    "driver": "Шофьор",
    "driver_name": "Шофьор",
    "qty": "Количество",
    "trailer": "Ремарке",
    "driver_phone": "Телефон на шофьора",
}


def sender_phone(session: Session, email: InboundEmail) -> str | None:
    """Телефонът за „Обади се“: контактът на писмото, иначе контрагентът; `None` ако няма."""
    contact = session.get(Contact, email.contact_id) if email.contact_id else None
    if contact is not None and contact.phone:
        return contact.phone
    counterparty = session.get(Counterparty, email.counterparty_id) if email.counterparty_id else None
    return counterparty.phone if counterparty is not None and counterparty.phone else None


def xlsx_rows(path: str) -> int | None:
    """Редове с данни (без заглавния и празните) в първия лист; `None` ако файлът не се чете."""
    try:
        wb = load_workbook(Path(path), read_only=True, data_only=True)
        try:
            ws = wb.worksheets[0]
            filled = sum(1 for row in ws.iter_rows(values_only=True) if any(c not in (None, "") for c in row))
        finally:
            wb.close()
    except Exception:
        return None
    return max(filled - 1, 0)


def _attachment_dto(a: EmailAttachment) -> EmailAttachmentDTO:
    rows = (
        xlsx_rows(a.storage_path) if a.content_type == XLSX or a.filename.lower().endswith(".xlsx") else None
    )
    return EmailAttachmentDTO(
        id=a.id, filename=a.filename, content_type=a.content_type, size_bytes=a.size_bytes, rows=rows
    )


def _uncertain(session: Session, request: ClientRequest) -> list[UncertainDTO]:
    lines = session.scalars(
        select(ClientRequestLine)
        .where(ClientRequestLine.request_id == request.id, ClientRequestLine.is_removed.is_(False))
        .order_by(ClientRequestLine.line_no)
    ).all()
    out: list[UncertainDTO] = []
    for line in lines:
        where = f"на {line.plate_raw}" if line.plate_raw else f"на ред {line.line_no}"
        out.extend(
            UncertainDTO(label=f"{FIELD_LABELS.get(f, f)} {where}", why=line.uncertain_note)
            for f in line.uncertain_fields
        )
    return out


def get_email(session: Session, email_id: int) -> EmailDTO:
    email = session.get(InboundEmail, email_id)
    if email is None:
        raise DomainError("not_found", "Писмото не е намерено.", http_status=404)
    counterparty = session.get(Counterparty, email.counterparty_id) if email.counterparty_id else None
    label = None
    if counterparty is not None:
        how = MATCH_LABELS.get(MatchMethod(email.match_method)) if email.match_method else None
        label = f"{counterparty.name} · {how}" if how else counterparty.name
    attachments = session.scalars(
        select(EmailAttachment)
        .where(EmailAttachment.inbound_email_id == email.id)
        .order_by(EmailAttachment.id)
    ).all()
    request = session.scalar(select(ClientRequest).where(ClientRequest.inbound_email_id == email.id))
    return EmailDTO(
        from_name=email.from_name,
        from_address=email.from_address,
        to=list(email.to_addresses),
        received_at=email.received_at,
        subject=email.subject,
        body_text=email.body_text,
        verified_sender=email.auth_ok,
        counterparty=IdName(id=counterparty.id, name=counterparty.name) if counterparty else None,
        match_label=label,
        call_phone=sender_phone(session, email),
        attachments=[_attachment_dto(a) for a in attachments],
        agent_notes=[AgentNoteDTO(**n) for n in (request.agent_notes or [])] if request else [],
        uncertain=_uncertain(session, request) if request else [],
    )


def get_attachment(session: Session, attachment_id: int) -> EmailAttachment:
    """Прикачен файл, чийто байтове още са на диска; иначе 404 (липсващ файл не е 500)."""
    attachment = session.get(EmailAttachment, attachment_id)
    if attachment is None or not Path(attachment.storage_path).is_file():
        raise DomainError("not_found", "Файлът не е намерен.", http_status=404)
    return attachment
