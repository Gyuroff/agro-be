"""Изходящи писма: чернова, undo, изпращане (docs/architecture.md §5, docs/api.md §2 „undo“)."""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import Settings
from app.mail import smtp
from app.models.cards import ActionCard, CardStatus
from app.models.company import CompanySettings
from app.models.mail import EmailAttachment, InboundEmail, OutboundEmail, OutboundPurpose, OutboundStatus
from app.services import numbering

log = logging.getLogger(__name__)

REF_SEQUENCE = "email_ref"
REF_WIDTH = 4

BeforeSendHook = Callable[[Session, OutboundEmail], None]
SendFailedHook = Callable[[int], None]
_before_send: dict[str, BeforeSendHook] = {}
_failed_hooks: list[SendFailedHook] = []


def register_before_send(purpose: OutboundPurpose, hook: BeforeSendHook) -> None:
    """Кука в транзакцията на `send_outbound`, преди SMTP (за заявки: `RequestService.finalize`)."""
    _before_send[purpose.value] = hook


def register_on_send_failed(hook: SendFailedHook) -> None:
    """Кука `hook(outbound_id)` СЛЕД commit на `failed` (пуш `send_failed`, T8.1)."""
    _failed_hooks.append(hook)


class OutboundService:
    @staticmethod
    def create_draft(
        session: Session,
        *,
        purpose: OutboundPurpose,
        to_addresses: list[str],
        subject: str,
        body_text: str,
        counterparty_id: int | None = None,
        cc_addresses: list[str] | None = None,
        body_html: str | None = None,
        in_reply_to_email_id: int | None = None,
    ) -> OutboundEmail:
        number = numbering.take_next(session, REF_SEQUENCE)
        ref = f"АИТ-{numbering.format_number(number, REF_WIDTH)}"
        outbound = OutboundEmail(
            reference_code=ref,
            purpose=purpose.value,
            counterparty_id=counterparty_id,
            to_addresses=to_addresses,
            cc_addresses=cc_addresses or [],
            subject=smtp.subject_with_reference(subject, ref),
            body_text=body_text,
            body_html=body_html,
            in_reply_to_email_id=in_reply_to_email_id,
        )
        session.add(outbound)
        session.flush()
        return outbound

    @staticmethod
    def queue(session: Session, outbound: OutboundEmail) -> OutboundEmail:
        """`draft` → `queued` със `send_after = now + email_undo_seconds`."""
        undo = session.scalar(select(CompanySettings.email_undo_seconds)) or 0
        outbound.status = OutboundStatus.QUEUED.value
        outbound.send_after = clock.now() + timedelta(seconds=undo)
        session.flush()
        return outbound

    @staticmethod
    def send_now(session: Session, outbound_id: int, settings: Settings) -> bool:
        """Тялото на `mail.send_outbound`. Лъжа = нищо за пращане (не е `queued`: undo/вече изпратено).

        SMTP грешка излиза като изключение; извикващият прави rollback и вика `mark_failed`.
        """
        outbound = session.scalar(
            select(OutboundEmail).where(OutboundEmail.id == outbound_id).with_for_update()
        )
        if outbound is None or outbound.status != OutboundStatus.QUEUED.value:
            return False
        company = session.scalars(select(CompanySettings)).one()
        hook = _before_send.get(outbound.purpose)
        if hook is not None:
            hook(session, outbound)
        parent = (
            session.get(InboundEmail, outbound.in_reply_to_email_id)
            if outbound.in_reply_to_email_id
            else None
        )
        files = [
            (a.filename, a.content_type, Path(a.storage_path).read_bytes())
            for a in session.scalars(
                select(EmailAttachment).where(EmailAttachment.outbound_email_id == outbound.id)
            )
        ]
        msg = smtp.build_message(outbound, company, in_reply_to=parent, attachments=files)
        smtp.send(msg, settings)
        outbound.status = OutboundStatus.SENT.value
        outbound.sent_at = clock.now()
        outbound.error = None
        session.flush()
        return True

    @staticmethod
    def mark_failed(session: Session, outbound_id: int, error: str) -> None:
        """Отделна транзакция след rollback: `failed` + грешка; свързаните карти пак са `open`."""
        outbound = session.scalar(
            select(OutboundEmail).where(OutboundEmail.id == outbound_id).with_for_update()
        )
        if outbound is None or outbound.status != OutboundStatus.QUEUED.value:
            return
        outbound.status = OutboundStatus.FAILED.value
        outbound.error = error[:2000]
        for card in session.scalars(select(ActionCard).where(ActionCard.outbound_email_id == outbound.id)):
            if card.status != CardStatus.OPEN.value:
                card.status = CardStatus.OPEN.value
                card.resolution = None
                card.resolved_at = None
                card.resolved_by_id = None
        session.flush()


def notify_send_failed(outbound_id: int) -> None:
    """Вика куките СЛЕД commit на `failed`; грешка в пуша не бива да губи вече записания статус."""
    for hook in _failed_hooks:
        try:
            hook(outbound_id)
        except Exception:
            log.exception("on_send_failed hook failed for outbound %s", outbound_id)


_SEND_PENDING_KEY = "pending_send_outbound"


def dispatch_send_outbound(outbound_id: int, eta: datetime | None) -> None:
    """Пуска `mail.send_outbound` с `eta` (регистрира се от `app.workers.tasks.mail`)."""
    from app.workers.celery_app import celery_app
    from app.workers.tasks import mail as _mail_tasks

    celery_app.tasks[_mail_tasks.SEND_TASK_NAME].apply_async((outbound_id,), eta=eta)


def schedule_send(session: Session, outbound: OutboundEmail) -> None:
    """Планира `mail.send_outbound` за `send_after`, но СЛЕД commit (иначе задачата не вижда писмото)."""
    session.info.setdefault(_SEND_PENDING_KEY, []).append((outbound.id, outbound.send_after))


def _send_after_commit(session: Session) -> None:
    for outbound_id, eta in session.info.pop(_SEND_PENDING_KEY, []):
        try:
            dispatch_send_outbound(outbound_id, eta)
        except Exception:
            log.exception("не успях да планирам изпращането на писмо %s", outbound_id)


def _send_after_rollback(session: Session) -> None:
    session.info.pop(_SEND_PENDING_KEY, None)


if not event.contains(Session, "after_commit", _send_after_commit):
    event.listen(Session, "after_commit", _send_after_commit)
if not event.contains(Session, "after_rollback", _send_after_rollback):
    event.listen(Session, "after_rollback", _send_after_rollback)
