"""Ядро на пощата (docs/architecture.md §5): всеки източник вика само `ingest_raw_email`."""

import logging
from collections.abc import Callable

from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import clock
from app.core.audit_context import AuditSource, actor_ctx_by_code, audit_context
from app.core.config import get_settings
from app.db import session as db_session
from app.mail.auth_results import parse_authentication_results
from app.mail.parse import ParsedEmail, extract_reference_code, parse
from app.mail.storage import save_bytes
from app.models.mail import EmailAttachment, InboundEmail

log = logging.getLogger(__name__)

PROCESS_TASK_NAME = "mail.process_email"
ACTOR_CODE = "system:mail_ingest"


def dispatch_process_email(email_id: int) -> None:
    """Пуска `mail.process_email` (регистрира се от `app.workers.tasks.mail`)."""
    from app.workers.celery_app import celery_app
    from app.workers.tasks import mail as _mail_tasks  # noqa: F401  (регистрира задачата и в API процеса)

    celery_app.tasks[PROCESS_TASK_NAME].delay(email_id)


def _store(session: Session, parsed: ParsedEmail, raw: bytes, source: str) -> int | None:
    if session.scalar(select(InboundEmail.id).where(InboundEmail.message_id == parsed.message_id)):
        return None

    auth = parse_authentication_results(parsed.headers, get_settings().mail_authserv_id)
    saved_raw = save_bytes("emails", raw)
    email = InboundEmail(
        message_id=parsed.message_id,
        in_reply_to=parsed.in_reply_to,
        references=parsed.references or None,
        from_address=parsed.from_address,
        from_name=parsed.from_name,
        to_addresses=parsed.to_addresses,
        cc_addresses=parsed.cc_addresses,
        subject=parsed.subject,
        body_text=parsed.body_text,
        body_html=parsed.body_html,
        received_at=parsed.date or clock.now(),
        raw_path=saved_raw.path,
        auth_spf=auth.spf,
        auth_dkim=auth.dkim,
        auth_dmarc=auth.dmarc,
        auth_ok=auth.auth_ok,
        reference_code=extract_reference_code(parsed.subject),
    )
    try:
        with session.begin_nested():  # дубликат от състезание → UNIQUE, без да рушим външната транзакция
            session.add(email)
            session.flush()
    except IntegrityError:
        log.info("дубликат по Message-ID %s (%s)", parsed.message_id, source)
        return None
    for att in parsed.attachments:
        suffix = "." + att.filename.rsplit(".", 1)[-1].lower() if "." in att.filename else ""
        saved = save_bytes("attachments", att.data, suffix=suffix)
        session.add(
            EmailAttachment(
                inbound_email_id=email.id,
                filename=att.filename,
                content_type=att.content_type,
                size_bytes=saved.size,
                sha256=saved.sha256,
                storage_path=saved.path,
            )
        )
    session.flush()
    return email.id


def ingest_raw_email(
    raw: bytes,
    source: str,
    *,
    session: Session | None = None,
    dispatch: Callable[[int], None] = dispatch_process_email,
) -> int | None:
    """Записва писмото; връща `inbound_emails.id` или `None` при дубликат (по `Message-ID`).

    Без `session` отваря собствена транзакция и пуска `mail.process_email` след commit. С подадена
    `session` само flush-ва; задачата се пуска след commit на викащия (`after_commit`).
    """
    parsed = parse(raw)
    if session is None:
        with (
            db_session.session_scope() as own,
            audit_context(
                actor_ctx_by_code(ACTOR_CODE, AuditSource.WORKER, reason=f"ingest:{source}", session=own)
            ),
        ):
            email_id = _store(own, parsed, raw, source)
        if email_id is not None:
            dispatch(email_id)
        return email_id

    email_id = _store(session, parsed, raw, source)
    if email_id is not None:
        event.listen(session, "after_commit", lambda _s: dispatch(email_id), once=True)
    return email_id
