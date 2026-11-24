"""Агент „фактура от Мега“ → запис `mega_invoices` (agro-6p5.7).

`handle` е обработчикът на `EmailKind.MEGA_INVOICE`. `mega_invoice_parser` само извлича; аритметиката е код:
сума на редовете = subtotal ±0.01, ДДС = Σ(ред × ставка) ±0.01, total = subtotal + ДДС ±0.01. Несъответствие,
липсващо поле или PDF без текстов слой → „неразчетена фактура“ (карта `unknown_sender`), без запис.
Клиентът е по ЕИК на получателя (`counterparty_id` е NULL, ако ЕИК не е познат). Идемпотентно по `number`.
"""

import logging
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import run_agent
from app.agents.inputs import pdf_text
from app.agents.mega_invoice_parser import graph as parser_graph
from app.agents.mega_invoice_parser.prompts import PROMPT_VERSION
from app.agents.mega_invoice_parser.schemas import MegaInvoiceParserOutput
from app.models.invoicing import MegaInvoice, MegaInvoiceSource
from app.models.mail import EmailAttachment, InboundEmail, InboundStatus
from app.models.masterdata import Counterparty
from app.services import inbound_mail

log = logging.getLogger(__name__)

UNREADABLE = "неразчетена фактура"
TOLERANCE = Decimal("0.01")
CENT = Decimal("0.01")


def check_arithmetic(out: MegaInvoiceParserOutput) -> list[str]:
    """Празен списък = фактурата е разчетена и сметките излизат."""
    problems: list[str] = []
    if not out.number:
        problems.append("няма номер")
    if out.issue_date is None:
        problems.append("няма дата")
    if not out.lines:
        problems.append("няма редове")
    if out.subtotal is None or out.vat_amount is None or out.total is None:
        problems.append("липсва данъчна основа, ДДС или сума")
    if problems or out.subtotal is None or out.vat_amount is None or out.total is None:
        return problems

    lines_sum = sum((line.amount for line in out.lines), Decimal(0))
    if abs(lines_sum - out.subtotal) > TOLERANCE:
        problems.append(f"редовете са {lines_sum}, данъчната основа е {out.subtotal}")
    vat = sum(
        ((line.amount * line.vat_rate / 100).quantize(CENT, ROUND_HALF_UP) for line in out.lines), Decimal(0)
    )
    if abs(vat - out.vat_amount) > TOLERANCE:
        problems.append(f"ДДС по редовете е {vat}, във фактурата е {out.vat_amount}")
    if abs(out.subtotal + out.vat_amount - out.total) > TOLERANCE:
        problems.append(f"основа + ДДС е {out.subtotal + out.vat_amount}, сумата е {out.total}")
    return problems


def _pdf(session: Session, email: InboundEmail) -> EmailAttachment | None:
    atts = session.scalars(
        select(EmailAttachment)
        .where(EmailAttachment.inbound_email_id == email.id)
        .order_by(EmailAttachment.id)
    )
    return next((a for a in atts if a.filename.lower().endswith(".pdf")), None)


def _unreadable(session: Session, email: InboundEmail, why: str) -> InboundStatus:
    log.warning("фактура от Мега, писмо %s: %s", email.id, why)
    inbound_mail._unknown_sender_card(session, email, f"{UNREADABLE}: {why}", email.counterparty_id)
    return InboundStatus.NEEDS_ACTION


def handle(session: Session, email: InboundEmail) -> InboundStatus:
    att = _pdf(session, email)
    if att is None:
        return _unreadable(session, email, "няма PDF")
    try:
        text = pdf_text(att.storage_path)
    except Exception as exc:  # повреден файл → човек
        return _unreadable(session, email, f"PDF не се чете ({type(exc).__name__})")
    if not text:
        return _unreadable(session, email, "PDF без текстов слой")

    outcome = run_agent(
        session,
        parser_graph.AGENT,
        ("inbound_emails", email.id),
        parser_graph.build_graph(),
        {"pdf_text": text},
        PROMPT_VERSION,
    )
    if not outcome.ok or not isinstance(outcome.output, MegaInvoiceParserOutput):
        raise RuntimeError(f"mega_invoice_parser: {outcome.error}")
    out = outcome.output

    if problems := check_arithmetic(out):
        return _unreadable(session, email, "; ".join(problems))
    assert out.number is not None
    if session.scalar(select(MegaInvoice.id).where(MegaInvoice.number == out.number)) is not None:
        return InboundStatus.PROCESSED  # същата фактура вече е записана (повторно писмо/копие)

    eik = "".join(ch for ch in (out.recipient_eik or "") if ch.isdigit()) or None
    counterparty_id = session.scalar(select(Counterparty.id).where(Counterparty.eik == eik)) if eik else None
    session.add(
        MegaInvoice(
            source=MegaInvoiceSource.EMAIL_COPY.value,
            inbound_email_id=email.id,
            attachment_id=att.id,
            number=out.number,
            issue_date=out.issue_date,
            counterparty_id=counterparty_id,
            recipient_eik=eik,
            lines=[line.model_dump(mode="json") for line in out.lines],
            subtotal=out.subtotal,
            vat_amount=out.vat_amount,
            total=out.total,
            agent_run_id=outcome.run_id,
        )
    )
    session.flush()
    return InboundStatus.PROCESSED


inbound_mail.KIND_HANDLERS[inbound_mail.EmailKind.MEGA_INVOICE] = handle
