"""Модели за поща (agro-l2v.6): ограничения, FK, одит."""

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import clock
from app.core.audit_context import AuditContext
from app.models.audit import AuditLog
from app.models.mail import EmailAttachment, InboundEmail, MailboxPoll, OutboundEmail


def _inbound(message_id: str = "<a@x>", **kw: Any) -> InboundEmail:
    fields: dict[str, object] = {
        "from_address": "maria@agrotrade.bg",
        "received_at": clock.now(),
        "raw_path": "/tmp/x.eml",
        "auth_ok": True,
        **kw,
    }
    return InboundEmail(message_id=message_id, **fields)


def _outbound(code: str = "АИТ-0001", **kw: Any) -> OutboundEmail:
    fields: dict[str, object] = {
        "purpose": "supplier_order",
        "to_addresses": ["a@b.bg"],
        "subject": f"Курсове [{code}]",
        "body_text": "текст",
        **kw,
    }
    return OutboundEmail(reference_code=code, **fields)


def test_duplicate_message_id_is_integrity_error(db_session: Session, audit_ctx: AuditContext) -> None:
    db_session.add(_inbound("<dup@x>"))
    db_session.flush()
    db_session.add(_inbound("<dup@x>"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_defaults_and_audit(db_session: Session, audit_ctx: AuditContext) -> None:
    mail = _inbound()
    db_session.add(mail)
    db_session.flush()
    assert mail.status == "new"
    assert mail.to_addresses == []
    assert db_session.scalars(select(AuditLog).where(AuditLog.entity_type == "inbound_emails")).all()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", "bogus"),
        ("doc_kind", "bogus"),
        ("match_method", "bogus"),
        ("reply_status", "bogus"),
        ("status", "bogus"),
        ("kind_confidence", 1.5),
    ],
)
def test_inbound_checks(db_session: Session, audit_ctx: AuditContext, field: str, value: object) -> None:
    extra: dict[str, Any] = {field: value}
    db_session.add(_inbound(**extra))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_outbound_defaults_unique_and_checks(db_session: Session, audit_ctx: AuditContext) -> None:
    out = _outbound()
    db_session.add(out)
    db_session.flush()
    assert out.status == "draft"
    with db_session.begin_nested():
        db_session.add(_outbound("АИТ-0001"))
        with pytest.raises(IntegrityError):
            db_session.flush()
    db_session.add(_outbound("АИТ-0002", purpose="bogus"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_outbound_message_id_unique_but_nullable(db_session: Session, audit_ctx: AuditContext) -> None:
    db_session.add_all([_outbound("АИТ-0003"), _outbound("АИТ-0004")])  # два NULL — позволено
    db_session.flush()
    db_session.add_all([_outbound("АИТ-0005", message_id="<m>"), _outbound("АИТ-0006", message_id="<m>")])
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_reply_links_both_ways(db_session: Session, audit_ctx: AuditContext) -> None:
    out = _outbound()
    db_session.add(out)
    db_session.flush()
    reply = _inbound("<r@x>", replies_to_outbound_id=out.id, kind="reply", reply_status="confirmed")
    db_session.add(reply)
    db_session.flush()
    out.in_reply_to_email_id = reply.id
    db_session.flush()


def test_attachment_exactly_one_parent(db_session: Session, audit_ctx: AuditContext) -> None:
    mail, out = _inbound(), _outbound()
    db_session.add_all([mail, out])
    db_session.flush()

    def att(**kw: int | None) -> EmailAttachment:
        return EmailAttachment(
            filename="f.xlsx", content_type="x", size_bytes=1, sha256="0" * 64, storage_path="/p", **kw
        )

    db_session.add(att(inbound_email_id=mail.id))
    db_session.add(att(outbound_email_id=out.id))
    db_session.flush()
    for bad in (att(), att(inbound_email_id=mail.id, outbound_email_id=out.id)):
        with db_session.begin_nested():
            db_session.add(bad)
            with pytest.raises(IntegrityError):
                db_session.flush()


def test_mailbox_polls_not_in_audit_log(db_session: Session, audit_ctx: AuditContext) -> None:
    now = clock.now()
    db_session.add(MailboxPoll(started_at=now, finished_at=now + timedelta(seconds=2), status="ok"))
    db_session.flush()
    assert not db_session.scalars(select(AuditLog).where(AuditLog.entity_type == "mailbox_polls")).all()
    db_session.add(MailboxPoll(started_at=now, status="bogus"))
    with pytest.raises(IntegrityError):
        db_session.flush()
