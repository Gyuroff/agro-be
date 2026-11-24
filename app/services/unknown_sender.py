"""Действия по карта „Непознат подател“ (docs/api.md §3): `link` и `ignore`.

`link` свързва писмото с контрагент (`match_method=manual`), по желание запомня адреса като контакт
(`source=learned`) и връща писмото в `new`; повторната обработка (`mail.process_email`) се пуска СЛЕД commit.
"""

import logging
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.domain.emails import normalize_email
from app.mail.ingest import dispatch_process_email
from app.models.cards import ActionCard, CardKind, CardResolution, CardStatus
from app.models.mail import InboundEmail, InboundStatus, MatchMethod
from app.models.masterdata import Contact, ContactSource, Counterparty
from app.services.cards import ActionResult, CardService, register_action

log = logging.getLogger(__name__)

_REPROCESS_KEY = "unknown_sender_reprocess"


class _LinkBody(BaseModel):
    counterparty_id: int
    remember: bool = False


def _email(session: Session, card: ActionCard) -> InboundEmail:
    email = (
        session.scalar(select(InboundEmail).where(InboundEmail.id == card.inbound_email_id).with_for_update())
        if card.inbound_email_id
        else None
    )
    if email is None:
        raise DomainError("not_found", "Картата няма писмо.", http_status=404)
    return email


def _link(session: Session, card: ActionCard, body: dict[str, Any]) -> ActionResult:
    try:
        data = _LinkBody.model_validate(body)
    except ValidationError as exc:
        fields = {".".join(map(str, e["loc"])): e["msg"] for e in exc.errors()}
        raise DomainError("validation_error", "Невалидни данни.", http_status=400, fields=fields) from exc
    if session.get(Counterparty, data.counterparty_id) is None:
        raise DomainError("not_found", "Контрагентът не е намерен.", http_status=404)
    email = _email(session, card)

    address = normalize_email(email.from_address)
    contact = session.scalar(select(Contact).where(Contact.email == address, Contact.is_active.is_(True)))
    if contact is not None and contact.counterparty_id != data.counterparty_id:
        raise DomainError("conflict", "Адресът вече е контакт на друг контрагент.", http_status=409)
    if data.remember and contact is None:
        contact = Contact(
            counterparty_id=data.counterparty_id, email=address, source=ContactSource.LEARNED.value
        )
        session.add(contact)
        session.flush()

    email.counterparty_id = data.counterparty_id
    email.contact_id = contact.id if contact is not None else None
    email.match_method = MatchMethod.MANUAL.value
    email.status = InboundStatus.NEW.value
    email.error = None
    email.processed_at = None
    session.flush()
    CardService.resolve(session, card, CardStatus.DONE, CardResolution.LINKED)
    session.info.setdefault(_REPROCESS_KEY, []).append(email.id)
    return ActionResult("Подателят е свързан")


def _ignore(session: Session, card: ActionCard, body: dict[str, Any]) -> ActionResult:
    email = _email(session, card)
    email.status = InboundStatus.IGNORED.value
    CardService.resolve(session, card, CardStatus.DISMISSED, CardResolution.IGNORED)
    return ActionResult("Писмото е игнорирано")


def _after_commit(session: Session) -> None:
    for email_id in session.info.pop(_REPROCESS_KEY, []):
        try:
            dispatch_process_email(email_id)
        except Exception:
            log.exception("не успях да планирам повторна обработка на писмо %s", email_id)


def _after_rollback(session: Session) -> None:
    session.info.pop(_REPROCESS_KEY, None)


if not event.contains(Session, "after_commit", _after_commit):
    event.listen(Session, "after_commit", _after_commit)
if not event.contains(Session, "after_rollback", _after_rollback):
    event.listen(Session, "after_rollback", _after_rollback)

register_action(CardKind.UNKNOWN_SENDER, "link", _link)
register_action(CardKind.UNKNOWN_SENDER, "ignore", _ignore)
