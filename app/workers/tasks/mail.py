"""Celery задачи на пощата (architecture §4)."""

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.mail.ingest import ACTOR_CODE, PROCESS_TASK_NAME, dispatch_process_email, ingest_raw_email
from app.services import inbound_mail, mailbox
from app.workers.audited import audited_task

POLL_TASK_NAME = "mail.poll_inbox"


@audited_task(ACTOR_CODE, name=PROCESS_TASK_NAME)
def process_email(session: Session, email_id: int) -> str | None:
    status = inbound_mail.process(session, email_id)
    return status.value if status else None


@audited_task(ACTOR_CODE, name=POLL_TASK_NAME)
def poll_inbox(session: Session) -> int:
    record = mailbox.poll(
        session, get_settings(), lambda raw: ingest_raw_email(raw, "imap"), dispatch_process_email
    )
    return record.fetched_count
