"""match_sender (agro-ha8.3): всяко ниво, приоритет, публични домейни, предположение."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import clock
from app.core.audit_context import AuditContext
from app.mail.matching import MatchResult, match_sender
from app.models.mail import InboundEmail, MatchMethod, OutboundEmail
from app.models.masterdata import Contact, Counterparty, CounterpartyDomain


def _cp(db: Session, eik: str, name: str = "Фирма") -> Counterparty:
    cp = Counterparty(name=name, legal_name=f"{name} ООД", eik=eik)
    db.add(cp)
    db.flush()
    return cp


def _mail(db: Session, **kw: Any) -> InboundEmail:
    fields: dict[str, Any] = {
        "message_id": "<in@x>",
        "from_address": "maria@agrotrade.bg",
        "received_at": clock.now(),
        "raw_path": "/tmp/x.eml",
        "auth_ok": True,
        **kw,
    }
    mail = InboundEmail(**fields)
    db.add(mail)
    db.flush()
    return mail


def _out(db: Session, cp: Counterparty | None, code: str = "АИТ-0001", mid: str | None = "<out@x>") -> None:
    db.add(
        OutboundEmail(
            reference_code=code,
            purpose="supplier_order",
            counterparty_id=cp.id if cp else None,
            to_addresses=["a@b.bg"],
            subject=f"Курсове [{code}]",
            body_text="т",
            message_id=mid,
        )
    )
    db.flush()


def test_thread_by_in_reply_to(db_session: Session, audit_ctx: AuditContext) -> None:
    cp = _cp(db_session, "111111111")
    _out(db_session, cp)
    mail = _mail(db_session, in_reply_to="<out@x>", from_address="someone@gmail.com")
    assert match_sender(db_session, mail) == MatchResult(cp.id, None, MatchMethod.THREAD)


def test_thread_by_references(db_session: Session, audit_ctx: AuditContext) -> None:
    cp = _cp(db_session, "111111111")
    _out(db_session, cp)
    mail = _mail(db_session, in_reply_to="<other@x>", references=["<old@x>", "<out@x>"])
    assert match_sender(db_session, mail).method == MatchMethod.THREAD


def test_subject_code(db_session: Session, audit_ctx: AuditContext) -> None:
    cp = _cp(db_session, "111111111")
    _out(db_session, cp, code="АИТ-0142", mid=None)
    mail = _mail(db_session, reference_code="АИТ-0142")
    assert match_sender(db_session, mail) == MatchResult(cp.id, None, MatchMethod.SUBJECT_CODE)


def test_contact(db_session: Session, audit_ctx: AuditContext) -> None:
    cp = _cp(db_session, "111111111")
    contact = Contact(counterparty_id=cp.id, email="Maria@AgroTrade.bg")
    db_session.add(contact)
    db_session.flush()
    assert match_sender(db_session, _mail(db_session)) == MatchResult(cp.id, contact.id, MatchMethod.CONTACT)


def test_inactive_contact_is_not_a_match(db_session: Session, audit_ctx: AuditContext) -> None:
    cp = _cp(db_session, "111111111")
    db_session.add(Contact(counterparty_id=cp.id, email="maria@agrotrade.bg", is_active=False))
    db_session.flush()
    result = match_sender(db_session, _mail(db_session))
    assert result.method is None and result.counterparty_id is None
    assert result.suggested_counterparty_id == cp.id


def test_domain(db_session: Session, audit_ctx: AuditContext) -> None:
    cp = _cp(db_session, "111111111")
    db_session.add(CounterpartyDomain(counterparty_id=cp.id, domain="agrotrade.bg"))
    db_session.flush()
    assert match_sender(db_session, _mail(db_session)) == MatchResult(cp.id, None, MatchMethod.DOMAIN)


def test_no_match(db_session: Session, audit_ctx: AuditContext) -> None:
    assert match_sender(db_session, _mail(db_session)) == MatchResult()


def test_contact_beats_domain(db_session: Session, audit_ctx: AuditContext) -> None:
    by_contact = _cp(db_session, "111111111")
    by_domain = _cp(db_session, "222222222")
    db_session.add(CounterpartyDomain(counterparty_id=by_domain.id, domain="agrotrade.bg"))
    db_session.add(Contact(counterparty_id=by_contact.id, email="maria@agrotrade.bg"))
    db_session.flush()
    result = match_sender(db_session, _mail(db_session))
    assert (result.counterparty_id, result.method) == (by_contact.id, MatchMethod.CONTACT)


def test_thread_beats_contact(db_session: Session, audit_ctx: AuditContext) -> None:
    by_thread = _cp(db_session, "111111111")
    by_contact = _cp(db_session, "222222222")
    _out(db_session, by_thread)
    db_session.add(Contact(counterparty_id=by_contact.id, email="maria@agrotrade.bg"))
    db_session.flush()
    result = match_sender(db_session, _mail(db_session, in_reply_to="<out@x>"))
    assert (result.counterparty_id, result.method) == (by_thread.id, MatchMethod.THREAD)


def test_outbound_without_counterparty_falls_through(db_session: Session, audit_ctx: AuditContext) -> None:
    cp = _cp(db_session, "111111111")
    _out(db_session, None)
    db_session.add(Contact(counterparty_id=cp.id, email="maria@agrotrade.bg"))
    db_session.flush()
    assert match_sender(db_session, _mail(db_session, in_reply_to="<out@x>")).method == MatchMethod.CONTACT


def test_public_domain_never_matches(db_session: Session, audit_ctx: AuditContext) -> None:
    cp = _cp(db_session, "111111111")
    # валидаторът не допуска публичен домейн през ORM → заобикаляме го, за да докажем защитата в запитването
    row = CounterpartyDomain(counterparty_id=cp.id, domain="example.bg")
    db_session.add(row)
    db_session.flush()
    db_session.execute(
        text("UPDATE counterparty_domains SET domain = 'gmail.com' WHERE id = :i"), {"i": row.id}
    )
    result = match_sender(db_session, _mail(db_session, from_address="x@gmail.com"))
    assert result == MatchResult()


def test_suggestion_from_inactive_domain(db_session: Session, audit_ctx: AuditContext) -> None:
    cp = _cp(db_session, "111111111")
    db_session.add(CounterpartyDomain(counterparty_id=cp.id, domain="agrotrade.bg", is_active=False))
    db_session.flush()
    result = match_sender(db_session, _mail(db_session))
    assert result == MatchResult(suggested_counterparty_id=cp.id)


def test_suggestion_from_similar_domain(db_session: Session, audit_ctx: AuditContext) -> None:
    cp = _cp(db_session, "111111111")
    db_session.add(CounterpartyDomain(counterparty_id=cp.id, domain="agrotrade.bg"))
    db_session.flush()
    result = match_sender(db_session, _mail(db_session, from_address="x@mail.agrotrade.bg"))
    assert result == MatchResult(suggested_counterparty_id=cp.id)
