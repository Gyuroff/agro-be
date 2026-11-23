"""Въпрос за плащане (agro-iat): писмо `payment_question` → карта; `reply_paid` / `reply_will_pay` → писмо
`payment_reply` в нишката на подателя. Нищо не се отбелязва като платено (docs/data-model.md §10).
"""

import re
from datetime import date
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.core import clock
from app.core.errors import DomainError
from app.mail import smtp
from app.models.cards import ActionCard, CardKind, CardResolution, CardStatus
from app.models.company import CompanySettings
from app.models.mail import InboundEmail, InboundStatus, OutboundEmail, OutboundPurpose, OutboundStatus
from app.models.masterdata import Counterparty
from app.schemas.cards import PaymentQuestionPayload
from app.services import inbound_mail
from app.services import outbound as outbound_service
from app.services.action_gateway import ProposalSpec
from app.services.cards import ActionResult, CardService, register_action
from app.services.outbound import OutboundService
from app.services.requests import make_undo_token

TITLE = "Въпрос за плащане от {name}"
_INVOICE_RE = re.compile(r"(?:фактур\w*|ф-р\w*|invoice)\s*(?:№|N|No\.?|#)?\s*(\d{3,})", re.IGNORECASE)


def find_invoice_number(subject: str, body: str) -> str | None:
    """Номер на фактура от текста („фактура № 4226“); няма → None (картата го показва като „без номер“)."""
    match = _INVOICE_RE.search(subject) or _INVOICE_RE.search(body)
    return match.group(1) if match else None


def handle(session: Session, email: InboundEmail) -> InboundStatus:
    counterparty = session.get(Counterparty, email.counterparty_id) if email.counterparty_id else None
    name = counterparty.name if counterparty else email.from_address
    CardService.create(
        session,
        CardKind.PAYMENT_QUESTION,
        title=TITLE.format(name=name),
        subtitle=email.subject,
        payload=PaymentQuestionPayload(
            inbound_email_id=email.id,
            counterparty_name=name,
            invoice_number=find_invoice_number(email.subject or "", email.body_text or ""),
            subject=email.subject or "",
            received_at=email.received_at,
        ),
        counterparty_id=email.counterparty_id,
        inbound_email_id=email.id,
        dedupe_key=f"payment_question:{email.id}",
    )
    return InboundStatus.NEEDS_ACTION


class _PaidBody(BaseModel):
    paid_on: date | None = None
    body_text: str | None = None
    attachment_ids: list[int] = []


class _WillPayBody(BaseModel):
    pay_by: date
    body_text: str | None = None


def _fmt(day: date) -> str:
    return day.strftime("%d.%m.%Y")


def _parse[T: BaseModel](model: type[T], body: dict[str, Any]) -> T:
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        fields = {".".join(map(str, e["loc"])): e["msg"] for e in exc.errors()}
        raise DomainError("validation_error", "Невалидни данни.", http_status=400, fields=fields) from exc


def _compose_reply(
    session: Session, card: ActionCard, default_text: str, custom: str | None
) -> tuple[InboundEmail, str, str]:
    """`(входящо писмо, тема, текст)` — единственото място, което сглобява отговора (и за предложението)."""
    inbound = session.get(InboundEmail, card.inbound_email_id) if card.inbound_email_id else None
    if inbound is None:
        raise DomainError("not_found", "Картата няма писмо.", http_status=404)
    text = (custom or "").strip() or default_text
    subject = inbound.subject or ""
    subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    return inbound, subject, text


def _reply(session: Session, card: ActionCard, default_text: str, custom: str | None) -> ActionResult:
    inbound, subject, text = _compose_reply(session, card, default_text, custom)
    existing = (
        session.get(OutboundEmail, card.outbound_email_id, with_for_update=True)
        if card.outbound_email_id
        else None
    )
    if existing is not None and existing.status == OutboundStatus.DRAFT.value:  # след „Отмени“: същото писмо
        outbound = existing
        outbound.body_text = text
    else:
        outbound = OutboundService.create_draft(
            session,
            purpose=OutboundPurpose.PAYMENT_REPLY,
            to_addresses=[inbound.from_address],
            subject=subject,
            body_text=text,
            counterparty_id=inbound.counterparty_id,
            in_reply_to_email_id=inbound.id,
        )
    OutboundService.queue(session, outbound)
    card.outbound_email_id = outbound.id
    CardService.resolve(session, card, CardStatus.DONE, CardResolution.SENT)
    outbound_service.schedule_send(session, outbound)
    return ActionResult("Отговорът е изпратен", make_undo_token([outbound.id]))


def _paid_reply(body: dict[str, Any]) -> tuple[str, str | None]:
    data = _parse(_PaidBody, body)
    if data.attachment_ids:
        raise DomainError(
            "validation_error",
            "Прикачени файлове към отговора още не се поддържат.",
            http_status=400,
            fields={"attachment_ids": "не се поддържа"},
        )
    paid_on = data.paid_on or clock.today_sofia()
    return f"Платено е на {_fmt(paid_on)}.", data.body_text


def _will_pay_reply(body: dict[str, Any]) -> tuple[str, str | None]:
    data = _parse(_WillPayBody, body)
    return f"Ще извършим плащането до {_fmt(data.pay_by)}.", data.body_text


def reply_paid(session: Session, card: ActionCard, body: dict[str, Any]) -> ActionResult:
    return _reply(session, card, *_paid_reply(body))


def reply_will_pay(session: Session, card: ActionCard, body: dict[str, Any]) -> ActionResult:
    return _reply(session, card, *_will_pay_reply(body))


def _proposal(
    session: Session, card: ActionCard, kind: str, default_text: str, custom: str | None
) -> ProposalSpec:
    inbound, subject, text = _compose_reply(session, card, default_text, custom)
    company = session.get(CompanySettings, 1)
    return ProposalSpec(
        kind=kind,
        title="Бих отговорила на въпроса за плащане",
        payload={
            "to": inbound.from_address,
            "subject": subject,
            "body_text": smtp.render_body(text, company.email_signature if company else ""),
        },
        deal_ref=f"inbound_emails:{inbound.id}",
    )


def propose_reply_paid(session: Session, card: ActionCard, body: dict[str, Any]) -> ProposalSpec:
    return _proposal(session, card, "reply_payment", *_paid_reply(body))


def propose_reply_will_pay(session: Session, card: ActionCard, body: dict[str, Any]) -> ProposalSpec:
    return _proposal(session, card, "reply_payment", *_will_pay_reply(body))


def ignore(session: Session, card: ActionCard, body: dict[str, Any]) -> ActionResult:
    CardService.resolve(session, card, CardStatus.DISMISSED, CardResolution.IGNORED)
    return ActionResult("Картата е игнорирана")


def register() -> None:
    register_action(CardKind.PAYMENT_QUESTION, "reply_paid", reply_paid, advisory=propose_reply_paid)
    register_action(
        CardKind.PAYMENT_QUESTION, "reply_will_pay", reply_will_pay, advisory=propose_reply_will_pay
    )
    register_action(CardKind.PAYMENT_QUESTION, "ignore", ignore)
    inbound_mail.KIND_HANDLERS[inbound_mail.EmailKind.PAYMENT_QUESTION] = handle


register()
