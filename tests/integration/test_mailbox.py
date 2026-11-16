"""IMAP poll, резерва за загубен dispatch и watchdog (agro-ha8.2). GreenMail от docker-compose."""

import smtplib
import uuid
from datetime import timedelta
from email.message import EmailMessage
from pathlib import Path

import pytest
from imapclient import IMAPClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core import clock
from app.core.audit_context import AuditContext
from app.core.config import Settings, get_settings
from app.mail.ingest import ingest_raw_email
from app.models.cards import ActionCard, CardKind, CardStatus
from app.models.company import CompanySettings
from app.models.mail import InboundEmail, InboundStatus, MailboxPoll, PollStatus
from app.services import mailbox

GM_HOST, GM_SMTP, GM_IMAP = "localhost", 3025, 3143
USER = "orders"


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("APP_IMAP_HOST", GM_HOST)
    monkeypatch.setenv("APP_IMAP_PORT", str(GM_IMAP))
    monkeypatch.setenv("APP_IMAP_SSL", "false")
    monkeypatch.setenv("APP_IMAP_USER", USER)
    monkeypatch.setenv("APP_IMAP_PASSWORD", USER)
    get_settings.cache_clear()


def _send(message_id: str) -> None:
    msg = EmailMessage()
    msg["From"] = "maria@agrotrade.example"
    msg["To"] = "orders@agroimport.test"
    msg["Subject"] = "Тест poll"
    msg["Message-ID"] = message_id
    msg.set_content("Заявка")
    with smtplib.SMTP(GM_HOST, GM_SMTP) as smtp:
        smtp.send_message(msg)


def _unseen_count(subject_id: str) -> int:
    with IMAPClient(GM_HOST, port=GM_IMAP, ssl=False) as c:
        c.login(USER, USER)
        c.select_folder("INBOX")
        return len(c.search(f'UNSEEN HEADER Message-ID "{subject_id}"'))


def _poll(db: Session, sent: list[int]) -> MailboxPoll:
    return mailbox.poll(
        db,
        get_settings(),
        lambda raw: ingest_raw_email(raw, "imap", session=db, dispatch=sent.append),
        sent.append,
    )


def test_poll_ingests_and_marks_seen(db_session: Session, audit_ctx: AuditContext) -> None:
    mid = f"<{uuid.uuid4()}@poll.test>"
    _send(mid)
    assert _unseen_count(mid) == 1
    sent: list[int] = []

    poll = _poll(db_session, sent)

    assert poll.status == PollStatus.OK.value
    assert poll.fetched_count >= 1
    assert db_session.scalar(select(InboundEmail).where(InboundEmail.message_id == mid)) is not None
    assert _unseen_count(mid) == 0


def test_connection_error_is_recorded_not_raised(
    db_session: Session, audit_ctx: AuditContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_IMAP_PORT", "1")  # нищо не слуша
    get_settings.cache_clear()

    poll = _poll(db_session, [])

    assert poll.status == PollStatus.ERROR.value
    assert poll.error
    assert poll.fetched_count == 0


def test_not_configured_is_error_poll(
    db_session: Session, audit_ctx: AuditContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APP_IMAP_HOST")
    get_settings.cache_clear()
    poll = _poll(db_session, [])
    assert poll.status == PollStatus.ERROR.value
    assert "не е конфигуриран" in (poll.error or "")


def test_failed_message_stays_unseen(db_session: Session, audit_ctx: AuditContext) -> None:
    mid = f"<{uuid.uuid4()}@fail.test>"
    _send(mid)

    def boom(raw: bytes) -> None:
        if mid.encode() in raw:
            raise ValueError("ingest счупен")

    from app.mail.sources.imap import poll_imap

    result = poll_imap(get_settings(), boom)
    assert any("ingest счупен" in e for e in result.errors)
    assert _unseen_count(mid) == 1
    # следващият poll го взема
    poll_imap(get_settings(), lambda raw: None)
    assert _unseen_count(mid) == 0


def test_stale_new_emails_are_redispatched(db_session: Session, audit_ctx: AuditContext) -> None:
    def mk(mid: str, age_min: int, status: InboundStatus) -> InboundEmail:
        e = InboundEmail(
            message_id=mid,
            from_address="a@b.bg",
            subject="s",
            body_text="t",
            received_at=clock.now(),
            raw_path="/tmp/x.eml",
            auth_ok=True,
            status=status.value,
        )
        db_session.add(e)
        db_session.flush()
        db_session.execute(
            update(InboundEmail.__table__)  # pyright: ignore[reportArgumentType]
            .where(InboundEmail.__table__.c.id == e.id)
            .values(created_at=clock.now() - timedelta(minutes=age_min))
        )
        return e

    old, fresh, done = (
        mk("<o@x>", 30, InboundStatus.NEW),
        mk("<f@x>", 1, InboundStatus.NEW),
        mk("<d@x>", 30, InboundStatus.PROCESSED),
    )
    sent: list[int] = []
    assert mailbox.requeue_stale_new(db_session, sent.append) == [old.id]
    assert sent == [old.id] and fresh.id not in sent and done.id not in sent


def _poll_row(db: Session, status: PollStatus, ago_min: int) -> None:
    at = clock.now() - timedelta(minutes=ago_min)
    db.add(MailboxPoll(started_at=at, finished_at=at, status=status.value, fetched_count=0))
    db.flush()


def _alarms(db: Session) -> list[ActionCard]:
    return list(db.scalars(select(ActionCard).where(ActionCard.kind == CardKind.MAILBOX_ALARM.value)))


def test_watchdog_one_card_then_superseded_on_ok(db_session: Session, audit_ctx: AuditContext) -> None:
    minutes = db_session.scalars(select(CompanySettings.mailbox_alarm_minutes)).one()
    assert not mailbox.check_watchdog(db_session)  # няма нито един poll
    _poll_row(db_session, PollStatus.OK, minutes + 15)
    _poll_row(db_session, PollStatus.ERROR, 1)

    assert mailbox.check_watchdog(db_session) is True
    assert mailbox.check_watchdog(db_session) is False  # dedupe
    (card,) = _alarms(db_session)
    assert card.status == CardStatus.OPEN.value
    assert card.payload["minutes"] >= minutes

    mailbox.record_poll(db_session, clock.now(), fetched=0)  # нов ok
    db_session.refresh(card)
    assert card.status == CardStatus.SUPERSEDED.value
    assert not mailbox.check_watchdog(db_session)
    assert len(_alarms(db_session)) == 1


def test_watchdog_quiet_when_recent_ok(db_session: Session, audit_ctx: AuditContext) -> None:
    _poll_row(db_session, PollStatus.OK, 5)
    assert not mailbox.check_watchdog(db_session)
    assert _alarms(db_session) == []


def test_settings_type() -> None:
    assert isinstance(get_settings(), Settings)
