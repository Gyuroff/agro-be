"""mail.process_email (agro-ha8.5): проверка, съпоставяне, вид, липсващ агент, грешка, идемпотентност."""

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.email_classifier.prompts import build_messages
from app.agents.llm import StubChatModel
from app.core import clock
from app.core.audit_context import AuditContext
from app.mail.ingest import PROCESS_TASK_NAME
from app.models.cards import ActionCard, AgentRun, CardKind
from app.models.mail import EmailKind, InboundEmail, InboundStatus, MatchMethod, OutboundEmail
from app.models.masterdata import Contact, Counterparty, CounterpartyDomain
from app.services import inbound_mail

BODY_OTHER = "Благодаря за поръчката, отбелязвам."
BODY_REQUEST = "Заявка седмица 47: 16.11 фуражовоз 20 т."


def _cp(db: Session, eik: str = "123456789", name: str = "Агро Фермер ООД") -> Counterparty:
    cp = Counterparty(name=name, legal_name=name, eik=eik)
    db.add(cp)
    db.flush()
    return cp


def _mail(db: Session, **kw: Any) -> InboundEmail:
    fields: dict[str, Any] = {
        "message_id": "<in@x>",
        "from_address": "maria@agro-fermer.bg",
        "subject": "Тема",
        "body_text": BODY_OTHER,
        "received_at": clock.now(),
        "raw_path": "/tmp/x.eml",
        "auth_ok": True,
        **kw,
    }
    mail = InboundEmail(**fields)
    db.add(mail)
    db.flush()
    return mail


def _known(db: Session) -> Counterparty:
    cp = _cp(db)
    db.add(CounterpartyDomain(counterparty_id=cp.id, domain="agro-fermer.bg"))
    db.flush()
    return cp


def _cards(db: Session) -> list[ActionCard]:
    return list(db.scalars(select(ActionCard)))


def _stub(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str, kind: str, conf: float) -> None:
    """Fixture за точния вход на класификатора, в временна папка (без файлове в репото)."""
    inp = {"from_address": "maria@agro-fermer.bg", "subject": "Тема", "body_text": body, "attachments": []}
    probe = StubChatModel("email_classifier", tmp_path)
    path = probe.fixture_path(build_messages(inp))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"kind": kind, "confidence": conf, "reason": "тест"}), encoding="utf-8")
    monkeypatch.setattr("app.agents.llm.get_chat_model", lambda agent: StubChatModel(agent, tmp_path))


def test_unverified_sender_card_without_agent(db_session: Session, audit_ctx: AuditContext) -> None:
    _known(db_session)
    mail = _mail(db_session, auth_ok=False)
    assert inbound_mail.process(db_session, mail.id) is InboundStatus.NEEDS_ACTION
    [card] = _cards(db_session)
    assert card.kind == CardKind.UNKNOWN_SENDER.value and card.subtitle == "непроверен подател"
    assert card.payload["auth_ok"] is False and card.inbound_email_id == mail.id
    assert db_session.scalar(select(func.count()).select_from(AgentRun)) == 0
    assert mail.processed_at is not None and mail.counterparty_id is None


def test_unknown_sender_card_with_domain_suggestion(db_session: Session, audit_ctx: AuditContext) -> None:
    cp = _cp(db_session)
    db_session.add(CounterpartyDomain(counterparty_id=cp.id, domain="agro-fermer.bg", is_active=False))
    db_session.flush()
    mail = _mail(db_session)
    assert inbound_mail.process(db_session, mail.id) is InboundStatus.NEEDS_ACTION
    [card] = _cards(db_session)
    assert card.payload["suggested_counterparty_id"] == cp.id
    assert card.payload["suggested_counterparty_name"] == "Агро Фермер ООД"
    assert card.payload["suggestion_reason"] == "domain" and card.payload["auth_ok"] is True
    assert db_session.scalar(select(func.count()).select_from(AgentRun)) == 0


def test_unknown_sender_without_suggestion(db_session: Session, audit_ctx: AuditContext) -> None:
    mail = _mail(db_session, from_address="x@nowhere.example")
    inbound_mail.process(db_session, mail.id)
    [card] = _cards(db_session)
    assert card.payload["suggested_counterparty_id"] is None and card.counterparty_id is None


def test_other_kind_is_processed_without_card(
    db_session: Session, audit_ctx: AuditContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cp = _known(db_session)
    _stub(monkeypatch, tmp_path, BODY_OTHER, "other", 0.9)
    mail = _mail(db_session)
    assert inbound_mail.process(db_session, mail.id) is InboundStatus.PROCESSED
    assert (mail.counterparty_id, mail.match_method, mail.kind) == (cp.id, "domain", "other")
    assert float(mail.kind_confidence or 0) == 0.9 and mail.error is None and mail.processed_at
    assert _cards(db_session) == []
    assert db_session.scalar(select(func.count()).select_from(AgentRun)) == 1


def test_second_call_does_nothing(
    db_session: Session, audit_ctx: AuditContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _known(db_session)
    _stub(monkeypatch, tmp_path, BODY_OTHER, "other", 0.9)
    mail = _mail(db_session)
    assert inbound_mail.process(db_session, mail.id) is InboundStatus.PROCESSED
    assert inbound_mail.process(db_session, mail.id) is None
    assert db_session.scalar(select(func.count()).select_from(AgentRun)) == 1
    assert inbound_mail.process(db_session, 999999) is None


def test_missing_kind_agent_needs_action(
    db_session: Session, audit_ctx: AuditContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _known(db_session)
    _stub(monkeypatch, tmp_path, BODY_REQUEST, "request", 0.95)
    monkeypatch.delitem(inbound_mail.KIND_HANDLERS, EmailKind.REQUEST, raising=False)
    mail = _mail(db_session, body_text=BODY_REQUEST)
    assert inbound_mail.process(db_session, mail.id) is InboundStatus.NEEDS_ACTION
    assert mail.kind == "request" and mail.error is None


def test_registered_handler_decides_status(
    db_session: Session, audit_ctx: AuditContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _known(db_session)
    _stub(monkeypatch, tmp_path, BODY_REQUEST, "request", 0.95)
    monkeypatch.setitem(inbound_mail.KIND_HANDLERS, EmailKind.REQUEST, lambda _s, _e: InboundStatus.PROCESSED)
    mail = _mail(db_session, body_text=BODY_REQUEST)
    assert inbound_mail.process(db_session, mail.id) is InboundStatus.PROCESSED


def test_low_confidence_raises_unknown_sender_card(
    db_session: Session, audit_ctx: AuditContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cp = _known(db_session)
    _stub(monkeypatch, tmp_path, BODY_REQUEST, "request", 0.5)
    mail = _mail(db_session, body_text=BODY_REQUEST)
    assert inbound_mail.process(db_session, mail.id) is InboundStatus.NEEDS_ACTION
    [card] = _cards(db_session)
    assert card.subtitle == "несигурен вид" and card.counterparty_id == cp.id and mail.kind == "other"


def test_failure_sets_failed_and_keeps_rest(
    db_session: Session, audit_ctx: AuditContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _known(db_session)
    _stub(monkeypatch, tmp_path, BODY_REQUEST, "request", 0.95)

    def boom(_s: Session, _e: InboundEmail) -> InboundStatus:
        raise ValueError("боом")

    monkeypatch.setitem(inbound_mail.KIND_HANDLERS, EmailKind.REQUEST, boom)
    mail = _mail(db_session, body_text=BODY_REQUEST)
    assert inbound_mail.process(db_session, mail.id) is InboundStatus.FAILED
    assert mail.status == "failed" and mail.error == "ValueError: боом" and mail.processed_at
    assert _cards(db_session) == []


def test_classifier_error_fails_email(db_session: Session, audit_ctx: AuditContext) -> None:
    _known(db_session)
    mail = _mail(db_session, body_text="няма fixture за този текст")
    assert inbound_mail.process(db_session, mail.id) is InboundStatus.FAILED
    assert mail.error and "email_classifier" in mail.error
    run = db_session.scalars(select(AgentRun)).one()
    assert run.status == "error"  # редът за грешката оцелява


def test_thread_match_records_reply_link(
    db_session: Session, audit_ctx: AuditContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cp = _cp(db_session)
    out = OutboundEmail(
        reference_code="АИТ-0001",
        purpose="supplier_order",
        counterparty_id=cp.id,
        to_addresses=["a@b.bg"],
        subject="Курсове [АИТ-0001]",
        body_text="т",
        message_id="<out@x>",
    )
    db_session.add(out)
    db_session.flush()
    _stub(monkeypatch, tmp_path, BODY_OTHER, "other", 0.9)
    mail = _mail(db_session, in_reply_to="<out@x>", from_address="maria@agro-fermer.bg")
    inbound_mail.process(db_session, mail.id)
    assert (mail.match_method, mail.contact_id, mail.replies_to_outbound_id) == (
        MatchMethod.THREAD.value,
        None,
        out.id,
    )
    assert mail.counterparty_id == cp.id


def test_contact_match(
    db_session: Session, audit_ctx: AuditContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cp = _cp(db_session)
    c = Contact(counterparty_id=cp.id, email="maria@agro-fermer.bg")
    db_session.add(c)
    db_session.flush()
    _stub(monkeypatch, tmp_path, BODY_OTHER, "other", 0.9)
    mail = _mail(db_session)
    inbound_mail.process(db_session, mail.id)
    assert (mail.match_method, mail.contact_id) == ("contact", c.id)


def test_ingest_dispatches_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.mail.ingest import dispatch_process_email
    from app.workers.celery_app import celery_app

    calls: list[int] = []
    monkeypatch.setattr(celery_app.tasks[PROCESS_TASK_NAME], "delay", lambda i: calls.append(i))
    dispatch_process_email(7)
    assert calls == [7]
