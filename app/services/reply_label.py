"""Етикет „отговор“ (docs/api.md §3): статус по `inbound_emails.replies_to_outbound_id`."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.mail import EmailKind, InboundEmail, OutboundEmail, ReplyStatus
from app.models.masterdata import Counterparty
from app.schemas.emails import ReplyDTO


def reply_label(session: Session, outbound_email_id: int | None) -> ReplyDTO | None:
    """`None`, ако няма изходящо писмо или то още не е тръгнало (няма на какво да се отговаря)."""
    outbound = session.get(OutboundEmail, outbound_email_id) if outbound_email_id else None
    if outbound is None or outbound.sent_at is None:
        return None
    replies = session.scalars(
        select(InboundEmail)
        .where(InboundEmail.replies_to_outbound_id == outbound.id)
        .order_by(InboundEmail.received_at, InboundEmail.id)
    ).all()
    counterparty = session.get(Counterparty, outbound.counterparty_id) if outbound.counterparty_id else None
    name = counterparty.name if counterparty is not None else None
    if not replies:
        return ReplyDTO(
            status="awaiting",
            counterparty_short_name=name,
            sent_at=outbound.sent_at,
            received_at=None,
            inbound_email_id=None,
            count=0,
        )
    last = replies[-1]
    confirmed = last.kind == EmailKind.REPLY.value and last.reply_status == ReplyStatus.CONFIRMED.value
    return ReplyDTO(
        status="confirmed" if confirmed else "needs_attention",
        counterparty_short_name=name,
        sent_at=outbound.sent_at,
        received_at=last.received_at,
        inbound_email_id=last.id,
        count=len(replies),
    )
