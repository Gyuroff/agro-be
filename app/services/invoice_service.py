"""Издаване на фактура в режим `erp` (docs/api.md §8, agro-66f.2).

`issue` — една транзакция: заключване на реда, номер от Мега (`app/invoicing/numbers.py`), дати, статус,
`trips.invoice_id` и събитие `invoiced`. PDF и писмото се правят СЛЕД commit (`invoicing.deliver_invoice`),
затова грешка там не връща номера: фактурата остава издадена, а `resend` / повторната доставка догонват.
"""

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.audit_context import get_audit_context
from app.core.errors import DomainError
from app.invoicing.numbers import format_invoice_number, get_number_provider
from app.invoicing.pdf import render_invoice_pdf
from app.mail import storage
from app.models.cards import ActionCard, CardKind, CardResolution, CardStatus
from app.models.company import CompanySettings
from app.models.invoicing import Invoice, InvoiceLine, InvoiceMode, InvoiceStatus, InvoiceTrip
from app.models.mail import EmailAttachment, OutboundEmail, OutboundPurpose
from app.models.trips import Trip, TripEvent, TripEventKind
from app.services import outbound as outbound_service
from app.services.action_gateway import ProposalSpec
from app.services.cards import ActionResult, CardService, register_action
from app.services.outbound import OutboundService

log = logging.getLogger(__name__)

DELIVER_TASK_NAME = "invoicing.deliver_invoice"
_PENDING_KEY = "pending_deliver_invoice"


def get_invoice(session: Session, invoice_id: int, *, lock: bool = False) -> Invoice:
    stmt = select(Invoice).where(Invoice.id == invoice_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    invoice = session.scalar(stmt)
    if invoice is None:
        raise DomainError("not_found", "Фактурата не съществува.", http_status=404)
    return invoice


def invoice_lines(session: Session, invoice: Invoice) -> list[InvoiceLine]:
    return list(
        session.scalars(
            select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id).order_by(InvoiceLine.line_no)
        )
    )


def _invalid_transition(invoice: Invoice) -> DomainError:
    return DomainError(
        "invalid_transition",
        f"Фактура в режим „{invoice.mode}“ със статус „{invoice.status}“ не може да се издаде.",
        http_status=409,
    )


class InvoiceService:
    @staticmethod
    def issue(session: Session, invoice_id: int, version: int) -> Invoice:
        invoice = get_invoice(session, invoice_id, lock=True)
        # Втори потребител, чакал заключването, вижда вече издадена фактура → 409.
        if invoice.mode != InvoiceMode.ERP.value or invoice.status != InvoiceStatus.DRAFT.value:
            raise _invalid_transition(invoice)
        if invoice.version != version:
            raise DomainError(
                "version_conflict", "Фактурата е променена от друг. Обновете и опитайте пак.", 409
            )

        number = get_number_provider().next_number()
        taken = session.scalar(
            select(Invoice.id).where(Invoice.number == number, Invoice.status == InvoiceStatus.ISSUED.value)
        )
        if taken is not None:
            raise DomainError(
                "number_taken", f"Номер {format_invoice_number(number)} вече е издаден.", http_status=409
            )

        today = clock.today_sofia()
        terms = (invoice.due_date - invoice.billing_date).days if invoice.due_date is not None else None
        invoice.number = number
        invoice.issue_date = today
        invoice.tax_event_date = today  # решение на собственика: днешна дата
        invoice.due_date = today + timedelta(days=terms) if terms is not None else None
        invoice.status = InvoiceStatus.ISSUED.value
        invoice.issued_at = clock.now()
        invoice.issued_by_id = get_audit_context().actor_id
        session.flush()

        display = format_invoice_number(number)
        trips = session.scalars(
            select(Trip)
            .join(InvoiceTrip, InvoiceTrip.trip_id == Trip.id)
            .where(InvoiceTrip.invoice_id == invoice.id)
        ).all()
        for trip in trips:
            trip.invoice_id = invoice.id
            session.add(
                TripEvent(
                    trip_id=trip.id,
                    occurred_at=clock.now(),
                    kind=TripEventKind.INVOICED.value,
                    text=f"Включен във фактура № {display}",
                )
            )
        session.flush()
        schedule_delivery(session, invoice.id)
        return invoice

    @staticmethod
    def deliver(session: Session, invoice_id: int) -> bool:
        """След commit на `issue`: PDF (ако липсва) и писмо до клиента (ако още няма). Идемпотентна.

        `False` — фактурата не е издадена (нищо за правене).
        """
        invoice = get_invoice(session, invoice_id, lock=True)
        if invoice.status != InvoiceStatus.ISSUED.value:
            return False
        InvoiceService._ensure_pdf(session, invoice)
        if invoice.outbound_email_id is None:
            InvoiceService._send_email(session, invoice)
        return True

    @staticmethod
    def resend(session: Session, invoice_id: int) -> Invoice:
        invoice = get_invoice(session, invoice_id, lock=True)
        if invoice.status != InvoiceStatus.ISSUED.value:
            raise _invalid_transition(invoice)
        InvoiceService._ensure_pdf(session, invoice)
        InvoiceService._send_email(session, invoice)
        return invoice

    @staticmethod
    def pdf_bytes(session: Session, invoice: Invoice) -> bytes:
        """PDF на издадена фактура — от файла; за чернова — наново, с воден знак."""
        if invoice.status == InvoiceStatus.ISSUED.value:
            InvoiceService._ensure_pdf(session, invoice)
            assert invoice.pdf_path is not None
            return _read(invoice.pdf_path)
        return render_invoice_pdf(invoice, invoice_lines(session, invoice))

    @staticmethod
    def _ensure_pdf(session: Session, invoice: Invoice) -> None:
        if invoice.pdf_path is not None:
            return
        data = render_invoice_pdf(invoice, invoice_lines(session, invoice))
        invoice.pdf_path = storage.save_bytes("invoices", data, suffix=".pdf").path
        session.flush()

    @staticmethod
    def _send_email(session: Session, invoice: Invoice) -> OutboundEmail:
        if not invoice.recipient_email:
            raise DomainError(
                "validation_error", "Клиентът няма имейл за фактури.", http_status=400, fields=None
            )
        assert invoice.number is not None and invoice.pdf_path is not None
        display = format_invoice_number(invoice.number)
        company = session.get_one(CompanySettings, 1)
        outbound = OutboundService.create_draft(
            session,
            purpose=OutboundPurpose.INVOICE,
            to_addresses=[invoice.recipient_email],
            subject=f"Фактура № {display}",
            body_text=f"Здравейте,\n\nВ прикачения файл е фактура № {display}.\n\n{company.email_signature}",
            counterparty_id=invoice.counterparty_id,
        )
        data = _read(invoice.pdf_path)
        saved = storage.save_bytes("attachments", data, suffix=".pdf")
        session.add(
            EmailAttachment(
                outbound_email_id=outbound.id,
                filename=f"Фактура-{display}.pdf",
                content_type="application/pdf",
                size_bytes=saved.size,
                sha256=saved.sha256,
                storage_path=saved.path,
            )
        )
        invoice.outbound_email_id = outbound.id
        OutboundService.queue(session, outbound)
        outbound_service.schedule_send(session, outbound)
        return outbound


def _read(path: str) -> bytes:
    from pathlib import Path

    return Path(path).read_bytes()


def _mark_emailed(session: Session, outbound: OutboundEmail) -> None:
    """Кука преди SMTP (същата транзакция): `emailed_at` се записва само ако писмото тръгне."""
    invoice = session.scalar(select(Invoice).where(Invoice.outbound_email_id == outbound.id))
    if invoice is not None:
        invoice.emailed_at = clock.now()


# --- доставката (PDF + писмо) се пуска след commit ---------------------------------------------------


def dispatch_deliver(invoice_id: int) -> None:
    from app.workers.celery_app import celery_app
    from app.workers.tasks import invoicing as _tasks  # noqa: F401  (регистрира задачата)

    celery_app.tasks[DELIVER_TASK_NAME].apply_async((invoice_id,))


def schedule_delivery(session: Session, invoice_id: int) -> None:
    session.info.setdefault(_PENDING_KEY, []).append(invoice_id)


def _after_commit(session: Session) -> None:
    for invoice_id in session.info.pop(_PENDING_KEY, []):
        try:
            dispatch_deliver(invoice_id)
        except Exception:
            log.exception("не успях да планирам доставката на фактура %s", invoice_id)


def _after_rollback(session: Session) -> None:
    session.info.pop(_PENDING_KEY, None)


if not event.contains(Session, "after_commit", _after_commit):
    event.listen(Session, "after_commit", _after_commit)
if not event.contains(Session, "after_rollback", _after_rollback):
    event.listen(Session, "after_rollback", _after_rollback)

outbound_service.register_before_send(OutboundPurpose.INVOICE, _mark_emailed)


# --- действие по карта invoice_due / issue (docs/api.md §2) -------------------------------------------


def _card_invoice_id(card: ActionCard) -> int:
    if card.invoice_id is None:
        raise DomainError("not_found", "Картата няма фактура.", http_status=404)
    return card.invoice_id


def _issue_version(body: dict[str, Any]) -> int:
    version = body.get("invoice_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise DomainError(
            "validation_error", "Липсва версия на фактурата.", 400, fields={"invoice_version": "required"}
        )
    return version


def _issue_action(session: Session, card: ActionCard, body: dict[str, Any]) -> ActionResult:
    invoice = InvoiceService.issue(session, _card_invoice_id(card), _issue_version(body))
    CardService.resolve(session, card, CardStatus.DONE, CardResolution.ISSUED)
    return ActionResult(f"Фактура № {format_invoice_number(invoice.number or 0)} е издадена")


def _issue_proposal(session: Session, card: ActionCard, body: dict[str, Any]) -> ProposalSpec:
    """Съветен режим: нищо не се издава и не се взема номер."""
    invoice = get_invoice(session, _card_invoice_id(card))
    return ProposalSpec(
        kind="issue_invoice",
        title=f"Бих издала фактура към {invoice.recipient_name}: {invoice.total} {invoice.currency}",
        payload={"invoice_id": invoice.id, "total": str(invoice.total), "recipient": invoice.recipient_name},
        deal_ref=f"invoices:{invoice.id}",
    )


register_action(CardKind.INVOICE_DUE, "issue", _issue_action, advisory=_issue_proposal)
