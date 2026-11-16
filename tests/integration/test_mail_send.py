"""mail.send_outbound (agro-ha8.4): GreenMail, undo, SMTP грешка."""

from collections.abc import Iterator
from contextlib import contextmanager
from email import message_from_bytes, policy
from email.message import EmailMessage
from pathlib import Path

import pytest
from imapclient import IMAPClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit_context import AuditContext, clear_actor_cache
from app.core.config import get_settings
from app.db import session as db_session_mod
from app.models.cards import ActionCard, CardKind, CardStatus
from app.models.company import CompanySettings
from app.models.mail import EmailAttachment, OutboundEmail, OutboundPurpose, OutboundStatus
from app.services.cards import CardService
from app.services.outbound import OutboundService, register_on_send_failed
from app.workers.tasks import mail as mail_tasks

GM_HOST, GM_SMTP, GM_IMAP = "localhost", 3025, 3143
RCPT = "supplier"  # GreenMail създава пощенска кутия при първо писмо; парола = логин
SYSTEM = "orders@agroimport.test"


@pytest.fixture(autouse=True)
def _env(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("APP_SMTP_HOST", GM_HOST)
    monkeypatch.setenv("APP_SMTP_PORT", str(GM_SMTP))
    monkeypatch.setenv("APP_SMTP_STARTTLS", "false")
    get_settings.cache_clear()
    clear_actor_cache()

    @contextmanager
    def fake_scope() -> Iterator[Session]:  # транзакцията на теста; изключение → rollback на savepoint
        with db_session.begin_nested():
            yield db_session

    monkeypatch.setattr(db_session_mod, "session_scope", fake_scope)
    yield
    get_settings.cache_clear()


@pytest.fixture
def company(db_session: Session, audit_ctx: AuditContext) -> CompanySettings:
    c = db_session.scalars(select(CompanySettings)).one()
    c.system_mailbox_address = SYSTEM
    c.short_name = "Агро Импорт"
    c.email_signature = "Поздрави,\nАгро Импорт"
    db_session.flush()
    return c


def _draft(db: Session, to: str, **kw: object) -> OutboundEmail:
    return OutboundService.create_draft(
        db,
        purpose=OutboundPurpose.SUPPLIER_ORDER,
        to_addresses=[to],
        subject="Заявка за камиони",
        body_text="Здравейте",
        **kw,  # type: ignore[arg-type]
    )


def _fetch(user: str, ref: str) -> EmailMessage:
    with IMAPClient(GM_HOST, port=GM_IMAP, ssl=False) as c:
        c.login(user, user)
        c.select_folder("INBOX")
        uids = c.search("ALL")  # кутията е уникална за теста
        assert len(uids) == 1, f"очаквано едно писмо за {ref}"
        raw = c.fetch(uids, ["BODY.PEEK[]"])[uids[0]][b"BODY[]"]
    return message_from_bytes(raw, policy=policy.default)  # type: ignore[return-value]


def test_sends_with_reference_message_id_and_signature(
    db_session: Session, company: CompanySettings, tmp_path: Path
) -> None:
    user = f"sup{abs(hash(tmp_path)) % 10**8}"
    out = _draft(db_session, f"{user}@example.test")
    f = tmp_path / "a.xlsx"
    f.write_bytes(b"PK-data")
    db_session.add(
        EmailAttachment(
            outbound_email_id=out.id,
            filename="a.xlsx",
            content_type="application/octet-stream",
            size_bytes=7,
            sha256="x",
            storage_path=str(f),
        )
    )
    OutboundService.queue(db_session, out)
    assert out.reference_code.startswith("АИТ-") and len(out.reference_code) == 8

    assert mail_tasks.send_outbound.delay(out.id).get() is True

    db_session.refresh(out)
    assert out.status == OutboundStatus.SENT.value and out.sent_at is not None
    msg = _fetch(f"{user}@example.test", out.reference_code)
    assert f"[{out.reference_code}]" in msg["Subject"]
    number = out.reference_code[-4:]
    assert msg["Message-ID"].startswith(f"<AIT-{number}.") and msg["Message-ID"].endswith("@agroimport.test>")
    assert msg["Message-ID"] == out.message_id
    assert "Поздрави,\nАгро Импорт" in msg.get_body(("plain",)).get_content().replace("\r\n", "\n")  # type: ignore[union-attr]
    assert [p.get_filename() for p in msg.iter_attachments()] == ["a.xlsx"]


def test_undo_before_task_does_not_send(db_session: Session, company: CompanySettings) -> None:
    out = _draft(db_session, "undo@example.test")
    OutboundService.queue(db_session, out)
    out.status = OutboundStatus.DRAFT.value  # undo
    db_session.flush()
    assert mail_tasks.send_outbound.delay(out.id).get() is False
    db_session.refresh(out)
    assert out.status == OutboundStatus.DRAFT.value and out.message_id is None


def test_smtp_error_marks_failed_reopens_card_and_notifies(
    db_session: Session, company: CompanySettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_SMTP_PORT", "1")  # нищо не слуша
    get_settings.cache_clear()
    out = _draft(db_session, "x@example.test")
    OutboundService.queue(db_session, out)
    card, _ = CardService.create_or_get(
        db_session,
        CardKind.MAILBOX_ALARM,
        title="t",
        payload={"last_ok_at": None, "minutes": 1},
        outbound_email_id=out.id,
    )
    card.status = CardStatus.DONE.value
    db_session.flush()
    notified: list[int] = []
    register_on_send_failed(notified.append)

    assert mail_tasks.send_outbound.delay(out.id).get() is False

    db_session.refresh(out)
    db_session.refresh(card)
    assert out.status == OutboundStatus.FAILED.value and out.error and "ConnectionRefused" in out.error
    assert card.status == CardStatus.OPEN.value
    assert notified == [out.id]
    assert db_session.scalars(select(ActionCard).where(ActionCard.outbound_email_id == out.id)).one() is card
