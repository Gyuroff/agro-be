"""Състояние на пощенската кутия: poll-ове, резерва за загубен dispatch, watchdog (architecture §4)."""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import Settings
from app.mail.sources.imap import PollResult, poll_imap
from app.models.cards import ActionCard, CardKind, CardStatus
from app.models.company import CompanySettings
from app.models.mail import InboundEmail, InboundStatus, MailboxPoll, PollStatus
from app.schemas.cards import MailboxAlarmPayload
from app.services.cards import CardService

log = logging.getLogger(__name__)

ALARM_KEY = "mailbox_alarm"
ALARM_TITLE = "Пощата не се проверява"
STALE_NEW_MINUTES = 10


def record_poll(
    session: Session, started_at: datetime, *, fetched: int, error: str | None = None
) -> MailboxPoll:
    poll = MailboxPoll(
        started_at=started_at,
        finished_at=clock.now(),
        status=(PollStatus.ERROR if error else PollStatus.OK).value,
        fetched_count=fetched,
        error=error,
    )
    session.add(poll)
    session.flush()
    if not error:
        supersede_alarm(session)
    return poll


def requeue_stale_new(
    session: Session, dispatch: Callable[[int], None], *, older_than_minutes: int = STALE_NEW_MINUTES
) -> list[int]:
    """Резерва: писма `new` по-стари от ~10 мин (загубен `.delay`) се пускат пак; `process` е идемпотентна."""
    cutoff = clock.now() - timedelta(minutes=older_than_minutes)
    ids = list(
        session.scalars(
            select(InboundEmail.id)
            .where(InboundEmail.status == InboundStatus.NEW.value, InboundEmail.created_at < cutoff)
            .order_by(InboundEmail.id)
        )
    )
    for email_id in ids:
        dispatch(email_id)
    return ids


def _open_alarm(session: Session) -> ActionCard | None:
    return session.scalar(
        select(ActionCard).where(
            ActionCard.dedupe_key == ALARM_KEY, ActionCard.status == CardStatus.OPEN.value
        )
    )


def supersede_alarm(session: Session) -> None:
    card = _open_alarm(session)
    if card is not None:
        card.status = CardStatus.SUPERSEDED.value
        card.resolved_at = clock.now()
        session.flush()


def check_watchdog(session: Session) -> bool:
    """Няма ok poll от `mailbox_alarm_minutes` → една отворена карта `mailbox_alarm` (истина, ако е нова)."""
    minutes = session.scalar(select(CompanySettings.mailbox_alarm_minutes))
    if minutes is None:
        raise RuntimeError("липсват company_settings")
    last_ok = session.scalar(
        select(func.max(MailboxPoll.finished_at)).where(MailboxPoll.status == PollStatus.OK.value)
    )
    # Без нито един ok броим от първия опит: празна таблица = още нищо не е пускано, няма какво да алармираме.
    baseline = last_ok or session.scalar(select(func.min(MailboxPoll.started_at)))
    if baseline is None or clock.now() - baseline < timedelta(minutes=minutes):
        return False
    if _open_alarm(session) is not None:
        return False
    silent = int((clock.now() - baseline).total_seconds() // 60)
    _, created = CardService.create_or_get(
        session,
        CardKind.MAILBOX_ALARM,
        title=ALARM_TITLE,
        subtitle=f"Не е проверявана от {silent} мин.",
        payload=MailboxAlarmPayload(last_ok_at=last_ok, minutes=silent),
        dedupe_key=ALARM_KEY,
    )
    return created


def poll(
    session: Session,
    settings: Settings,
    handle: Callable[[bytes], object],
    dispatch: Callable[[int], None],
) -> MailboxPoll:
    """Един poll: чете пощата, записва ред в `mailbox_polls` (изключение навън не излиза), пуска резервата."""
    started = clock.now()
    result = PollResult()
    error: str | None = None
    try:
        result = poll_imap(settings, handle)
        if result.errors:
            error = "; ".join(result.errors[:5])
    except Exception as exc:
        log.exception("poll на пощата се провали")
        error = f"{type(exc).__name__}: {exc}"
    record = record_poll(session, started, fetched=result.fetched, error=error)
    requeue_stale_new(session, dispatch)
    return record
