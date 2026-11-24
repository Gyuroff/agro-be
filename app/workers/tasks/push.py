"""Пуш задачи (architecture §4): опашка при нова карта, изпращане на минута, обобщение в 07:00."""

import logging

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.push.fcm import FcmSender
from app.push.sender import PushSender
from app.services import push
from app.workers.audited import audited_task

log = logging.getLogger(__name__)

ACTOR = "system:scheduler"

push.register_hooks()  # воркерът също създава карти (поща) → пушът трябва да е закачен


def build_senders() -> dict[str, PushSender]:
    """Доставчици по `push_notifications.provider`. HMS идва с agro-zvb.2."""
    senders: dict[str, PushSender] = {}
    fcm = FcmSender.from_settings(get_settings())
    if fcm is not None:
        senders["fcm"] = fcm
    return senders


@audited_task(ACTOR, name="push.enqueue_card")
def enqueue_card(session: Session, card_id: int) -> int:
    return len(push.enqueue_for_card(session, card_id))


@audited_task(ACTOR, name="push.enqueue_send_failed")
def enqueue_send_failed(session: Session, outbound_id: int) -> int:
    return len(push.enqueue_send_failed(session, outbound_id))


@audited_task(ACTOR, name="push.dispatch_due")
def dispatch_due(session: Session) -> dict[str, int]:
    return push.dispatch_due(session, build_senders())


@audited_task(ACTOR, name="push.morning_digest")
def morning_digest(session: Session) -> int:
    digests = push.morning_digest(session)
    push.dispatch_due(session, build_senders())
    return len(digests)
