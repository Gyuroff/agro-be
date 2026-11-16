"""Писмо „Потвърждавам графика“ към клиента (agro-cmf): ПОСЛЕДНАТА стъпка след доставчиците.

Прикаченият файл е по правилата на адаптера на клиента (`counterparties.client_file_adapter`,
`app/adapters/client_files`): клиент без адаптер получава само писмото. Адаптерът не налучква —
предупрежденията му се показват в превюто и файлът тръгва само ако е попълнен поне един ред.
"""

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.adapters.client_files import FillResult, LineAssignment, get_adapter
from app.core.errors import DomainError
from app.domain.plates import format_plate
from app.mail import smtp
from app.mail.storage import save_bytes
from app.models.company import CompanySettings
from app.models.contracts import Contract
from app.models.mail import EmailAttachment, InboundEmail, OutboundEmail, OutboundPurpose
from app.models.masterdata import Counterparty, Warehouse
from app.models.trips import ClientRequest, RequestStatus
from app.services import outbound as outbound_service
from app.services.outbound import OutboundService
from app.services.requests import RequestService

GREETING = "Здравейте,\nПотвърждавам графика."
ATTACHED = "\nПрилагам файла с попълнено място на товарене."


@dataclass
class ConfirmationDraft:
    to: str
    subject: str
    body_text: str
    inbound: InboundEmail
    client: Counterparty
    attachment: tuple[str, bytes] | None = None
    filled: int = 0
    warnings: list[str] = field(default_factory=list)


def _assignments(session: Session, request: ClientRequest) -> list[LineAssignment]:
    out: list[LineAssignment] = []
    for line in RequestService.lines(session, request):
        warehouse = session.get(Warehouse, line.warehouse_id) if line.warehouse_id else None
        supplier = session.get(Counterparty, warehouse.counterparty_id) if warehouse else None
        out.append(
            LineAssignment(
                source_row=line.source_row,
                planned_date=line.planned_date,
                plate="" if line.own_transport else format_plate(line.plate_raw),
                driver_name=line.driver_name,
                qty_t=line.planned_qty_t,
                own_transport=line.own_transport,
                warehouse_name=warehouse.name if warehouse else None,
                supplier_name=supplier.name if supplier else None,
            )
        )
    return out


def _fill(session: Session, request: ClientRequest, client: Counterparty) -> FillResult | None:
    adapter = get_adapter(client.client_file_adapter)
    if adapter is None:
        return None
    source = (
        session.get(EmailAttachment, request.source_attachment_id) if request.source_attachment_id else None
    )
    if source is None:
        return FillResult(warnings=["няма оригинален файл от клиента — пращаме само писмото"])
    return adapter.fill(Path(source.storage_path), _assignments(session, request))


class ClientConfirmationService:
    @staticmethod
    def draft(session: Session, request: ClientRequest) -> ConfirmationDraft:
        if request.status != RequestStatus.SENT.value:
            raise DomainError(
                "invalid_transition",
                "Клиентът се потвърждава след като всички писма до доставчиците са тръгнали.",
                http_status=409,
            )
        inbound = session.get(InboundEmail, request.inbound_email_id)
        sale = session.get(Contract, request.sale_contract_id) if request.sale_contract_id else None
        client = session.get(Counterparty, sale.counterparty_id) if sale is not None else None
        if inbound is None or client is None:
            raise DomainError("validation_error", "Заявката няма клиент.", http_status=400)
        result = _fill(session, request, client)
        body = GREETING
        draft = ConfirmationDraft(
            to=inbound.from_address,
            subject=f"Re: {inbound.subject or 'заявка'}".replace("Re: Re:", "Re:"),
            body_text=body,
            inbound=inbound,
            client=client,
        )
        if result is not None:
            draft.warnings = result.warnings
            draft.filled = result.filled
            if result.data is not None:
                source = session.get(EmailAttachment, request.source_attachment_id)
                assert source is not None
                draft.attachment = (source.filename, result.data)
                draft.body_text = body + ATTACHED
        return draft

    @staticmethod
    def preview(session: Session, request: ClientRequest) -> dict[str, object]:
        draft = ClientConfirmationService.draft(session, request)
        company = session.get(CompanySettings, 1)
        signature = company.email_signature if company is not None else ""
        return {
            "to": draft.to,
            "subject": smtp.subject_with_reference(draft.subject, "АИТ-…"),
            "body_text": smtp.render_body(draft.body_text, signature),
            "attachment": (
                {"filename": draft.attachment[0], "filled_rows": draft.filled} if draft.attachment else None
            ),
            "warnings": draft.warnings,
        }

    @staticmethod
    def send(session: Session, request: ClientRequest) -> OutboundEmail:
        RequestService.get(session, request.id, lock=True)
        if request.confirmation_outbound_id is not None:
            raise DomainError("invalid_transition", "Клиентът вече е потвърден.", http_status=409)
        draft = ClientConfirmationService.draft(session, request)
        outbound = OutboundService.create_draft(
            session,
            purpose=OutboundPurpose.CLIENT_CONFIRMATION,
            to_addresses=[draft.to],
            subject=draft.subject,
            body_text=draft.body_text,
            counterparty_id=draft.client.id,
            in_reply_to_email_id=draft.inbound.id,
        )
        if draft.attachment is not None:
            filename, data = draft.attachment
            saved = save_bytes("attachments", data, suffix=Path(filename).suffix.lower())
            session.add(
                EmailAttachment(
                    outbound_email_id=outbound.id,
                    filename=filename,
                    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    size_bytes=saved.size,
                    sha256=saved.sha256,
                    storage_path=saved.path,
                )
            )
        OutboundService.queue(session, outbound)
        request.confirmation_outbound_id = outbound.id
        session.flush()
        outbound_service.schedule_send(session, outbound)
        return outbound
