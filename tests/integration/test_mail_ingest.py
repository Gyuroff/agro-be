"""ingest_raw_email (agro-ha8.1): дубликати, auth, файлове, dispatch след commit."""

from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit_context import AuditContext
from app.core.config import get_settings
from app.mail.ingest import ingest_raw_email
from app.models.mail import EmailAttachment, InboundEmail

FIX = Path(__file__).parent.parent / "fixtures" / "emails" / "synthetic"


@pytest.fixture(autouse=True)
def _mail_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("APP_MAIL_AUTHSERV_ID", "mx.test")
    get_settings.cache_clear()


def _raw(name: str) -> bytes:
    return (FIX / name).read_bytes()


def test_ingest_stores_email_attachment_and_eml(db_session: Session, audit_ctx: AuditContext) -> None:
    sent: list[int] = []
    raw = _raw("multipart_xlsx.eml")
    email_id = ingest_raw_email(raw, "test", session=db_session, dispatch=sent.append)
    assert email_id is not None
    row = db_session.get_one(InboundEmail, email_id)
    assert (row.subject, row.from_address, row.auth_ok, row.status) == (
        "Камиони седмица 47",
        "maria@agrotrade.example",
        True,
        "new",
    )
    assert Path(row.raw_path).read_bytes() == raw
    [att] = db_session.scalars(select(EmailAttachment)).all()
    assert att.filename == "Камиони_седмица_47.xlsx" and att.inbound_email_id == email_id
    assert Path(att.storage_path).read_bytes() == b"PK\x03\x04fake-xlsx"
    assert sent == []  # още няма commit


def test_duplicate_returns_none_without_second_row(db_session: Session, audit_ctx: AuditContext) -> None:
    raw = _raw("cyrillic_subject.eml")
    assert ingest_raw_email(raw, "test", session=db_session, dispatch=lambda _i: None) is not None
    assert ingest_raw_email(raw, "test", session=db_session, dispatch=lambda _i: None) is None
    assert db_session.scalar(select(func.count()).select_from(InboundEmail)) == 1


def test_reference_code_and_auth_flags(db_session: Session, audit_ctx: AuditContext) -> None:
    ids = {
        n: ingest_raw_email(_raw(n), "test", session=db_session, dispatch=lambda _i: None)
        for n in ("cyrillic_subject.eml", "spf_fail.eml", "foreign_authserv.eml", "no_message_id.eml")
    }
    rows = {n: db_session.get_one(InboundEmail, i) for n, i in ids.items() if i}
    assert rows["cyrillic_subject.eml"].reference_code == "АИТ-0142"
    assert rows["spf_fail.eml"].auth_ok is False and rows["spf_fail.eml"].auth_spf == "fail"
    assert rows["foreign_authserv.eml"].auth_ok is False and rows["foreign_authserv.eml"].auth_spf is None
    assert rows["no_message_id.eml"].message_id.endswith("@local>")


def test_dispatch_after_commit(db_session: Session, audit_ctx: AuditContext) -> None:
    sent: list[int] = []
    email_id = ingest_raw_email(_raw("html_only.eml"), "test", session=db_session, dispatch=sent.append)
    db_session.commit()
    assert sent == [email_id]


def test_process_email_task_is_registered() -> None:
    from app.mail.ingest import PROCESS_TASK_NAME
    from app.workers.celery_app import celery_app

    celery_app.loader.import_default_modules()  # както го прави worker-ът (`include`)
    assert PROCESS_TASK_NAME in celery_app.tasks
