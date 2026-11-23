"""Нови видове писмо (agro-iat): payment_question → карта и отговор в нишката; reply → етикет; doc_kind."""

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.email_classifier.prompts import build_messages
from app.agents.llm import StubChatModel
from app.core import clock
from app.core.audit_context import AuditContext
from app.core.errors import DomainError
from app.models.cards import ActionCard, CardKind, CardResolution, CardStatus
from app.models.mail import (
    DocKind,
    InboundEmail,
    InboundStatus,
    OutboundEmail,
    OutboundPurpose,
    OutboundStatus,
    ReplyStatus,
)
from app.models.masterdata import Counterparty, CounterpartyDomain
from app.services import inbound_mail
from app.services.cards import CardService
from app.services.payment_questions import find_invoice_number
from app.services.requests import RequestService

SUBJECT = "фактура в падеж"
BODY = "Моля, проверете дали фактура № 4226 е платена."


def _mail(db: Session) -> InboundEmail:
    cp = Counterparty(name="Амар Спед", legal_name="Амар Спед ООД", eik="111222333")
    db.add(cp)
    db.flush()
    db.add(CounterpartyDomain(counterparty_id=cp.id, domain="amar.bg"))
    mail = InboundEmail(
        message_id="<pq@x>",
        from_address="acc@amar.bg",
        subject=SUBJECT,
        body_text=BODY,
        received_at=clock.now(),
        raw_path="/tmp/x.eml",
        auth_ok=True,
    )
    db.add(mail)
    db.flush()
    return mail


def _stub(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, answer: dict[str, Any]) -> None:
    inp = {"from_address": "acc@amar.bg", "subject": SUBJECT, "body_text": BODY, "attachments": []}
    path = StubChatModel("email_classifier", tmp_path).fixture_path(build_messages(inp))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"confidence": 0.9, "reason": "тест", **answer}), encoding="utf-8")
    monkeypatch.setattr("app.agents.llm.get_chat_model", lambda agent: StubChatModel(agent, tmp_path))


def _card(db: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ActionCard:
    mail = _mail(db)
    _stub(monkeypatch, tmp_path, {"kind": "payment_question"})
    assert inbound_mail.process(db, mail.id) is InboundStatus.NEEDS_ACTION
    return db.scalars(select(ActionCard)).one()


def test_payment_question_creates_card(
    db_session: Session, audit_ctx: AuditContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    card = _card(db_session, monkeypatch, tmp_path)
    assert card.kind == CardKind.PAYMENT_QUESTION.value and card.title == "Въпрос за плащане от Амар Спед"
    assert card.payload["invoice_number"] == "4226" and card.payload["counterparty_name"] == "Амар Спед"
    assert card.dedupe_key == f"payment_question:{card.inbound_email_id}"


def test_reply_paid_queues_payment_reply_in_thread(
    db_session: Session, audit_ctx: AuditContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    card = _card(db_session, monkeypatch, tmp_path)
    _, result = CardService.perform_action(db_session, card.id, "reply_paid", {"paid_on": "2026-11-16"})
    out = db_session.scalars(select(OutboundEmail)).one()
    assert out.purpose == OutboundPurpose.PAYMENT_REPLY.value and out.status == OutboundStatus.QUEUED.value
    assert out.in_reply_to_email_id == card.inbound_email_id and out.to_addresses == ["acc@amar.bg"]
    assert out.subject.startswith("Re: фактура в падеж") and out.body_text == "Платено е на 16.11.2026."
    assert (card.status, card.resolution) == (CardStatus.DONE.value, CardResolution.SENT.value)
    assert card.outbound_email_id == out.id and result.undo_token


def test_reply_will_pay_and_custom_text(
    db_session: Session, audit_ctx: AuditContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    card = _card(db_session, monkeypatch, tmp_path)
    CardService.perform_action(db_session, card.id, "reply_will_pay", {"pay_by": "2026-11-20"})
    assert db_session.scalars(select(OutboundEmail)).one().body_text == "Ще извършим плащането до 20.11.2026."


def test_reply_paid_defaults_to_today_and_custom_body(
    db_session: Session, audit_ctx: AuditContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    card = _card(db_session, monkeypatch, tmp_path)
    CardService.perform_action(db_session, card.id, "reply_paid", {})
    out = db_session.scalars(select(OutboundEmail)).one()
    assert out.body_text == f"Платено е на {clock.today_sofia():%d.%m.%Y}."


def test_custom_body_text_wins(
    db_session: Session, audit_ctx: AuditContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    card = _card(db_session, monkeypatch, tmp_path)
    CardService.perform_action(db_session, card.id, "reply_paid", {"body_text": "Платихме вчера."})
    assert db_session.scalars(select(OutboundEmail)).one().body_text == "Платихме вчера."


def test_undo_reopens_card_and_resend_reuses_draft(
    db_session: Session, audit_ctx: AuditContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    card = _card(db_session, monkeypatch, tmp_path)
    _, result = CardService.perform_action(db_session, card.id, "reply_paid", {})
    assert result.undo_token
    RequestService.undo(db_session, result.undo_token)
    assert card.status == CardStatus.OPEN.value
    CardService.perform_action(db_session, card.id, "reply_will_pay", {"pay_by": "2026-11-25"})
    out = db_session.scalars(select(OutboundEmail)).one()  # същото писмо, без нов АИТ номер
    assert out.status == OutboundStatus.QUEUED.value and "25.11.2026" in out.body_text


def test_ignore_dismisses_without_mail(
    db_session: Session, audit_ctx: AuditContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    card = _card(db_session, monkeypatch, tmp_path)
    CardService.perform_action(db_session, card.id, "ignore", {})
    assert (card.status, card.resolution) == (CardStatus.DISMISSED.value, CardResolution.IGNORED.value)
    assert db_session.scalars(select(OutboundEmail)).first() is None


def test_reply_validation(
    db_session: Session, audit_ctx: AuditContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    card = _card(db_session, monkeypatch, tmp_path)
    with pytest.raises(DomainError) as exc:
        CardService.perform_action(db_session, card.id, "reply_will_pay", {})
    assert exc.value.http_status == 400
    with pytest.raises(DomainError):
        CardService.perform_action(db_session, card.id, "reply_paid", {"attachment_ids": [1]})
    assert card.status == CardStatus.OPEN.value


def test_reply_kind_stores_status_and_needs_no_card(
    db_session: Session, audit_ctx: AuditContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mail = _mail(db_session)
    _stub(monkeypatch, tmp_path, {"kind": "reply", "reply_status": "needs_attention"})
    assert inbound_mail.process(db_session, mail.id) is InboundStatus.PROCESSED
    assert mail.kind == "reply" and mail.reply_status == ReplyStatus.NEEDS_ATTENTION.value
    assert mail.doc_kind is None and db_session.scalars(select(ActionCard)).first() is None


def test_supplier_doc_stores_doc_kind(
    db_session: Session, audit_ctx: AuditContext, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mail = _mail(db_session)
    _stub(monkeypatch, tmp_path, {"kind": "supplier_doc", "doc_kind": "proforma"})
    inbound_mail.process(db_session, mail.id)
    assert mail.doc_kind == DocKind.PROFORMA.value and mail.reply_status is None


@pytest.mark.parametrize(
    ("subject", "body", "expected"),
    [
        ("Фактура № 4226", "", "4226"),
        ("напомняне", "Моля проверете ф-ра 0012345 дали е платена", "0012345"),
        ("падеж", "фактурата е неплатена", None),
        ("Invoice #9001", "", "9001"),
    ],
)
def test_find_invoice_number(subject: str, body: str, expected: str | None) -> None:
    assert find_invoice_number(subject, body) == expected
