"""Съпоставяне на подателя (docs/mvp.md „Съпоставяне на входящите имейли“, нива 1–2).

Реда: нишка/номер в темата → контакт → домейн → нищо. Видът на писмото се определя другаде.
"""

from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.domain.emails import domain_of, is_public_domain, normalize_email
from app.models.mail import InboundEmail, MatchMethod, OutboundEmail
from app.models.masterdata import Contact, CounterpartyDomain


@dataclass(frozen=True)
class MatchResult:
    counterparty_id: int | None = None
    contact_id: int | None = None
    method: MatchMethod | None = None
    suggested_counterparty_id: int | None = None


def _by_thread(session: Session, email: InboundEmail) -> MatchResult | None:
    message_ids = [m for m in [email.in_reply_to, *(email.references or [])] if m]
    if message_ids:
        # най-близкият отговор (In-Reply-To) е първи в списъка; търсим по реда му
        found = {
            o.message_id: o
            for o in session.scalars(
                select(OutboundEmail).where(
                    OutboundEmail.message_id.in_(message_ids), OutboundEmail.counterparty_id.is_not(None)
                )
            )
        }
        for mid in message_ids:
            if mid in found:
                return MatchResult(found[mid].counterparty_id, None, MatchMethod.THREAD)
    if email.reference_code:
        out = session.scalar(
            select(OutboundEmail).where(
                OutboundEmail.reference_code == email.reference_code,
                OutboundEmail.counterparty_id.is_not(None),
            )
        )
        if out is not None:
            return MatchResult(out.counterparty_id, None, MatchMethod.SUBJECT_CODE)
    return None


def _suggest(session: Session, address: str, domain: str) -> int | None:
    """Предположение по неактивен контакт или неактивен/свързан домейн (без публични домейни)."""
    contact = session.scalar(
        select(Contact.counterparty_id)
        .where(Contact.email == address, Contact.is_active.is_(False))
        .order_by(Contact.id.desc())
    )
    if contact is not None:
        return contact
    if is_public_domain(domain):
        return None
    return session.scalar(
        select(CounterpartyDomain.counterparty_id)
        .where(
            or_(
                CounterpartyDomain.domain == domain,  # неактивен точен домейн
                CounterpartyDomain.domain.like(f"%.{domain}"),  # подобен: поддомейн на познат
                CounterpartyDomain.domain == domain.partition(".")[2],  # подобен: родителски домейн
            )
        )
        .order_by(CounterpartyDomain.is_active.desc(), CounterpartyDomain.id)
    )


def match_sender(session: Session, email: InboundEmail) -> MatchResult:
    if (result := _by_thread(session, email)) is not None:
        return result

    try:
        address = normalize_email(email.from_address)
        domain = domain_of(address)
    except ValueError:
        return MatchResult()

    contact = session.scalar(select(Contact).where(Contact.email == address, Contact.is_active.is_(True)))
    if contact is not None:
        return MatchResult(contact.counterparty_id, contact.id, MatchMethod.CONTACT)

    if not is_public_domain(domain):
        cp_id = session.scalar(
            select(CounterpartyDomain.counterparty_id).where(
                CounterpartyDomain.domain == domain, CounterpartyDomain.is_active.is_(True)
            )
        )
        if cp_id is not None:
            return MatchResult(cp_id, None, MatchMethod.DOMAIN)

    return MatchResult(suggested_counterparty_id=_suggest(session, address, domain))
