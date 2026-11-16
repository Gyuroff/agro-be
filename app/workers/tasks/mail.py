"""Celery задачи на пощата (architecture §4)."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

from celery import Task
from sqlalchemy.orm import Session

from app.core.audit_context import AuditSource, actor_ctx_by_code, audit_context
from app.core.config import get_settings
from app.db import session as db_session
from app.mail.ingest import ACTOR_CODE, PROCESS_TASK_NAME, dispatch_process_email, ingest_raw_email
from app.services import inbound_mail, mailbox, outbound
from app.workers.audited import audited_task
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)

POLL_TASK_NAME = "mail.poll_inbox"
SEND_TASK_NAME = "mail.send_outbound"
SEND_ACTOR_CODE = "system:scheduler"


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


@contextmanager
def _audited_scope(request_id: str | None) -> Iterator[Session]:
    with db_session.session_scope() as session:
        ctx = actor_ctx_by_code(
            SEND_ACTOR_CODE,
            AuditSource.WORKER,
            request_id=request_id,
            reason=f"task:{SEND_TASK_NAME}",
            session=session,
        )
        with audit_context(ctx):
            yield session


@celery_app.task(bind=True, name=SEND_TASK_NAME)
def _send_outbound(self: Task, outbound_id: int) -> bool:
    """`mail.send_outbound`: пращане в една транзакция; при грешка rollback + `failed` в отделна."""
    settings = get_settings()
    try:
        with _audited_scope(self.request.id) as session:
            return outbound.OutboundService.send_now(session, outbound_id, settings)
    except Exception as exc:
        log.exception("изпращането на писмо %s се провали", outbound_id)
        with _audited_scope(self.request.id) as session:
            outbound.OutboundService.mark_failed(session, outbound_id, f"{type(exc).__name__}: {exc}")
        outbound.notify_send_failed(outbound_id)
        return False


send_outbound = cast(Task, _send_outbound)
