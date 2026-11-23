"""Обработка на входящо писмо (agro-ha8.5): проверка → съпоставяне → вид → агент за вида → карта.

Единственото място, което сменя `inbound_emails.status`. Не отваря транзакция и не прави commit:
това е работа на `mail.process_email` (`app/workers/tasks/mail.py`).
"""

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import run_agent
from app.agents.email_classifier.graph import AGENT as CLASSIFIER
from app.agents.email_classifier.graph import build_graph, decide
from app.agents.email_classifier.prompts import PROMPT_VERSION
from app.agents.email_classifier.schemas import ClassifierOutput
from app.agents.inputs import clean_email_text, date_window, xlsx_to_tsv
from app.core import clock
from app.mail.matching import MatchResult, match_sender
from app.models.cards import CardKind
from app.models.mail import (
    EmailAttachment,
    EmailKind,
    InboundEmail,
    InboundStatus,
    MatchMethod,
    OutboundEmail,
)
from app.models.masterdata import Counterparty
from app.schemas.cards import UnknownSenderPayload
from app.services.cards import CardService

log = logging.getLogger(__name__)

UNVERIFIED_REASON = "непроверен подател"
UNKNOWN_TITLE = "Имейл от непознат подател"

# Обработчик на вид: (session, email) → нов статус на писмото. Агентите за видовете се регистрират тук
# от своите тикети (6p5.4 request, 6p5.5 report, 6p5.6 cancellation, 6p5.7 mega_invoice).
KindHandler = Callable[[Session, InboundEmail], InboundStatus]
KIND_HANDLERS: dict[EmailKind, KindHandler] = {}


def _unknown_sender_card(
    session: Session, email: InboundEmail, reason: str, suggested_id: int | None = None
) -> None:
    name = (
        session.scalar(select(Counterparty.name).where(Counterparty.id == suggested_id))
        if suggested_id
        else None
    )
    payload = UnknownSenderPayload(
        inbound_email_id=email.id,
        from_address=email.from_address,
        subject=email.subject or "",
        suggested_counterparty_id=suggested_id,
        suggested_counterparty_name=name,
        suggestion_reason="domain" if suggested_id else None,
        auth_ok=email.auth_ok,
    )
    CardService.create(
        session,
        CardKind.UNKNOWN_SENDER,
        title=UNKNOWN_TITLE,
        subtitle=reason,
        payload=payload,
        counterparty_id=suggested_id,
        inbound_email_id=email.id,
        dedupe_key=f"unknown_sender:{email.id}",
    )


def _apply_match(session: Session, email: InboundEmail, match: MatchResult) -> None:
    email.counterparty_id = match.counterparty_id
    email.contact_id = match.contact_id
    email.match_method = match.method.value if match.method else None
    if match.method in (MatchMethod.THREAD, MatchMethod.SUBJECT_CODE):
        email.replies_to_outbound_id = _outbound_id(session, email)


def _outbound_id(session: Session, email: InboundEmail) -> int | None:
    ids = [m for m in [email.in_reply_to, *(email.references or [])] if m]
    if ids:
        found = {
            mid: oid
            for oid, mid in session.execute(
                select(OutboundEmail.id, OutboundEmail.message_id).where(OutboundEmail.message_id.in_(ids))
            )
        }
        for mid in ids:
            if mid in found:
                return found[mid]
    if email.reference_code:
        return session.scalar(
            select(OutboundEmail.id).where(OutboundEmail.reference_code == email.reference_code)
        )
    return None


def email_agent_input(session: Session, email: InboundEmail, *, row_numbers: bool = False) -> dict[str, Any]:
    attachments: list[dict[str, str]] = []
    for att in session.scalars(select(EmailAttachment).where(EmailAttachment.inbound_email_id == email.id)):
        item = {"name": att.filename}
        if att.filename.lower().endswith(".xlsx"):
            try:
                item["tsv"] = xlsx_to_tsv(
                    att.storage_path, window=date_window(email.received_at.date()), row_numbers=row_numbers
                )
            except Exception:  # повреден файл не бива да спира обработката; агентът го вижда само по името
                log.warning("xlsx %s на писмо %s не се чете", att.filename, email.id)
        attachments.append(item)
    return {
        "from_address": email.from_address,
        "subject": email.subject or "",
        "body_text": clean_email_text(email.body_text or ""),
        "attachments": attachments,
    }


def _classify(session: Session, email: InboundEmail) -> bool:
    """Записва `kind`/`kind_confidence`. False → агентът се провали (грешка за извикващия)."""
    outcome = run_agent(
        session,
        CLASSIFIER,
        ("inbound_emails", email.id),
        build_graph(),
        email_agent_input(session, email),
        PROMPT_VERSION,
    )
    if not outcome.ok or not isinstance(outcome.output, ClassifierOutput):
        raise RuntimeError(f"email_classifier: {outcome.error}")
    decision = decide(outcome.output)
    email.kind = decision.kind.value
    email.kind_confidence = outcome.output.confidence  # type: ignore[assignment]
    email.doc_kind = decision.doc_kind.value if decision.doc_kind else None
    email.reply_status = decision.reply_status.value if decision.reply_status else None
    if decision.uncertain:
        _unknown_sender_card(session, email, decision.reason, email.counterparty_id)
        return False
    return True


def process(session: Session, email_id: int) -> InboundStatus | None:
    """Обработва писмо със status `new`; всичко друго е no-op (връща None). Идемпотентна."""
    email = session.scalar(select(InboundEmail).where(InboundEmail.id == email_id).with_for_update())
    if email is None or email.status != InboundStatus.NEW.value:
        return None
    email.status = InboundStatus.PROCESSING.value
    email.error = None
    try:
        status = _run(session, email)
    except Exception as exc:
        log.exception("mail.process_email: писмо %s не се обработи", email_id)
        email.status = InboundStatus.FAILED.value
        email.error = f"{type(exc).__name__}: {exc}"[:2000]
        email.processed_at = clock.now()
        session.flush()
        return InboundStatus.FAILED
    email.status = status.value
    email.processed_at = clock.now()
    session.flush()
    return status


def _run(session: Session, email: InboundEmail) -> InboundStatus:
    if not email.auth_ok:
        _unknown_sender_card(session, email, UNVERIFIED_REASON)
        return InboundStatus.NEEDS_ACTION

    match = match_sender(session, email)
    _apply_match(session, email, match)
    if match.counterparty_id is None:
        _unknown_sender_card(session, email, "непознат подател", match.suggested_counterparty_id)
        return InboundStatus.NEEDS_ACTION

    if not _classify(session, email):
        return InboundStatus.NEEDS_ACTION
    kind = EmailKind(email.kind)
    if kind is EmailKind.OTHER:
        return InboundStatus.PROCESSED
    handler = KIND_HANDLERS.get(kind)
    if handler is None:
        log.warning("няма агент за вид %s (писмо %s) → needs_action", kind.value, email.id)
        return InboundStatus.NEEDS_ACTION
    with session.begin_nested():  # недовършените записи на агента не оцеляват
        return handler(session, email)


def _handle_reply(session: Session, email: InboundEmail) -> InboundStatus:
    """Отговор на наше писмо: без карта. `kind`/`reply_status`/`replies_to_outbound_id` са вече записани;
    етикетът „отговор“ се смята от тях при четене (docs/api.md)."""
    return InboundStatus.PROCESSED


KIND_HANDLERS[EmailKind.REPLY] = _handle_reply

from app.services import payment_questions, request_intake  # noqa: E402,F401  (регистрират обработчиците)
