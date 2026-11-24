"""Пуш известия (docs/push.md): опашка `push_notifications`, изпращане, обобщение, send_failed.

- `enqueue_for_card` (куката `on_card_created`) и `enqueue_send_failed` пишат редове `queued`.
- `dispatch_due` изпраща дължимите (картата затворена → `skipped`; мъртъв токен → изчистен).
- `morning_digest` събира отложените неспешни от тихите часове в едно P12 на устройство.
Неспешна нощна нотификация е „отложена“ = насрочена точно за края на тихите часове (`rules.is_deferred`).
"""

import logging
from collections.abc import Mapping
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.models.cards import ActionCard, CardStatus, PushKind, PushNotification, PushStatus
from app.models.company import CompanySettings
from app.models.identity import Device, User
from app.models.identity import PushProvider as DeviceProvider
from app.models.mail import OutboundEmail
from app.models.masterdata import Counterparty
from app.models.trips import ClientRequest, Trip
from app.push import rules
from app.push.sender import InvalidToken, PushMessage, PushSender

log = logging.getLogger(__name__)

DIGEST_TITLES = 3
DEFERRED_GRACE = timedelta(minutes=30)  # ако обобщението не е тръгнало, отложените излизат поотделно


def _recipients(session: Session) -> list[Device]:
    return list(
        session.scalars(
            select(Device)
            .join(User, User.id == Device.user_id)
            .where(
                User.is_active.is_(True),
                User.push_enabled.is_(True),
                Device.revoked_at.is_(None),
                Device.push_token.is_not(None),
                Device.push_provider.in_([DeviceProvider.FCM.value, DeviceProvider.HMS.value]),
            )
            .order_by(Device.id)
        )
    )


def _route(card_id: int) -> str:
    return f"/dashboard?card={card_id}"


def _queue(
    session: Session,
    *,
    kind: str,
    title: str,
    body: str,
    urgent: bool,
    card_id: int | None,
    when: datetime,
) -> list[PushNotification]:
    data = {"kind": kind, "route": _route(card_id) if card_id else "/dashboard"}
    if card_id:
        data["card_id"] = str(card_id)
    rows = [
        PushNotification(
            kind=kind,
            is_urgent=urgent,
            user_id=device.user_id,
            device_id=device.id,
            action_card_id=card_id,
            title=title,
            body=body,
            data=data,
            provider=device.push_provider,
            scheduled_for=when,
        )
        for device in _recipients(session)
    ]
    session.add_all(rows)
    session.flush()
    return rows


def _schedule(session: Session, urgent: bool) -> datetime:
    settings = session.get_one(CompanySettings, 1)
    return rules.schedule_for(clock.now(), settings.push_quiet_from, settings.push_quiet_to, urgent=urgent)


def enqueue_for_card(session: Session, card_id: int) -> list[PushNotification]:
    """Един пуш на карта: без шаблон, затворена, вече пращана/опашка → нищо."""
    card = session.get(ActionCard, card_id)
    if card is None or card.status != CardStatus.OPEN.value or card.push_sent_at is not None:
        return []
    if not rules.has_template(card.kind):
        return []
    already = session.scalar(
        select(PushNotification.id).where(
            PushNotification.action_card_id == card_id, PushNotification.kind == card.kind
        )
    )
    if already is not None:
        return []
    name = session.scalar(select(Counterparty.name).where(Counterparty.id == card.counterparty_id))
    suppliers: list[str] = []
    if card.client_request_id is not None:
        suppliers = list(
            session.scalars(
                select(Counterparty.name)
                .join(Trip, Trip.supplier_id == Counterparty.id)
                .where(Trip.request_id == card.client_request_id)
                .distinct()
                .order_by(Counterparty.name)
            )
        )
    title, body = rules.render(
        card.kind, rules.card_fields(card, counterparty_name=name, suppliers=suppliers)
    )
    urgent = rules.is_urgent(card.kind)
    return _queue(
        session,
        kind=card.kind,
        title=title,
        body=body,
        urgent=urgent,
        card_id=card.id,
        when=_schedule(session, urgent),
    )


def enqueue_send_failed(session: Session, outbound_id: int) -> list[PushNotification]:
    """P4: имейлът към доставчика не е изпратен; картата заявка е вече отворена наново."""
    outbound = session.get(OutboundEmail, outbound_id)
    if outbound is None:
        return []
    card = session.scalar(
        select(ActionCard)
        .where(ActionCard.outbound_email_id == outbound_id)
        .order_by(ActionCard.id.desc())
        .limit(1)
    )
    if card is None:
        log.warning("send_failed: няма карта за писмо %s, пуш не се праща", outbound_id)
        return []
    supplier = session.scalar(
        select(Counterparty.name).where(Counterparty.id == outbound.counterparty_id)
    ) or ", ".join(outbound.to_addresses)
    request = session.get(ClientRequest, card.client_request_id) if card.client_request_id else None
    if request is not None and request.period_from and request.period_to:
        period = f"{rules.fmt_day(request.period_from)}–{rules.fmt_day(request.period_to)}"
    else:
        period = card.subtitle or "заявката"
    title, body = rules.render(PushKind.SEND_FAILED.value, {"supplier": supplier, "period": period})
    return _queue(
        session,
        kind=PushKind.SEND_FAILED.value,
        title=title,
        body=body,
        urgent=True,
        card_id=card.id,
        when=clock.now(),
    )


def _is_deferred(session: Session, row: PushNotification) -> bool:
    if row.kind == PushKind.DIGEST.value:
        return False
    quiet_to = session.get_one(CompanySettings, 1).push_quiet_to
    return rules.is_deferred(row.scheduled_for, quiet_to, urgent=row.is_urgent)


def dispatch_due(session: Session, senders: Mapping[str, PushSender]) -> dict[str, int]:
    """`push.dispatch_due`: изпраща `queued` със `scheduled_for ≤ now`. Връща броячи по статус."""
    now = clock.now()
    due = session.scalars(
        select(PushNotification)
        .where(PushNotification.status == PushStatus.QUEUED.value, PushNotification.scheduled_for <= now)
        .order_by(PushNotification.id)
        .with_for_update(skip_locked=True)
    ).all()
    counts = {s.value: 0 for s in PushStatus}
    for row in due:
        if _is_deferred(session, row) and now - row.scheduled_for < DEFERRED_GRACE:
            continue  # чака обобщението в 07:00
        card = session.get(ActionCard, row.action_card_id) if row.action_card_id else None
        if card is not None and card.status != CardStatus.OPEN.value:
            row.status = PushStatus.SKIPPED.value
        else:
            _send(session, row, card, senders, now)
        counts[row.status] += 1
    session.flush()
    return counts


def _send(
    session: Session,
    row: PushNotification,
    card: ActionCard | None,
    senders: Mapping[str, PushSender],
    now: datetime,
) -> None:
    device = session.get_one(Device, row.device_id)
    sender = senders.get(row.provider)
    if sender is None:
        row.status, row.error = PushStatus.FAILED.value, f"няма изпращач за {row.provider}"
        return
    if device.push_token is None:
        row.status, row.error = PushStatus.FAILED.value, "устройството е без токен"
        return
    message = PushMessage(
        title=row.title,
        body=row.body,
        data={k: str(v) for k, v in row.data.items()},
        is_urgent=row.is_urgent,
        collapse_key=f"card-{row.action_card_id}" if row.action_card_id else None,
    )
    try:
        row.provider_message_id = sender.send(device.push_token, message)
    except InvalidToken as exc:
        device.push_token = None
        device.push_provider = DeviceProvider.NONE.value
        row.status, row.error = PushStatus.FAILED.value, f"невалиден токен: {exc}"[:2000]
        return
    except Exception as exc:
        log.exception("пуш %s не мина", row.id)
        row.status, row.error = PushStatus.FAILED.value, str(exc)[:2000]
        return
    row.status, row.sent_at = PushStatus.SENT.value, now
    if card is not None and card.push_sent_at is None:
        card.push_sent_at = now


def morning_digest(session: Session) -> list[PushNotification]:
    """`push.morning_digest` (07:00): отложените неспешни → едно P12 на устройство; техните редове `skipped`.

    Затворените междувременно карти не се броят. Обобщението се праща от `dispatch_due`.
    """
    now = clock.now()
    deferred = [
        row
        for row in session.scalars(
            select(PushNotification)
            .where(PushNotification.status == PushStatus.QUEUED.value, PushNotification.scheduled_for <= now)
            .order_by(PushNotification.id)
            .with_for_update(skip_locked=True)
        )
        if _is_deferred(session, row)
    ]
    by_device: dict[int, list[tuple[PushNotification, ActionCard]]] = {}
    for row in deferred:
        card = session.get(ActionCard, row.action_card_id) if row.action_card_id else None
        row.status = PushStatus.SKIPPED.value
        if card is None or card.status != CardStatus.OPEN.value:
            continue
        row.error = "в обобщението"
        if card.push_sent_at is None:
            card.push_sent_at = now
        by_device.setdefault(row.device_id, []).append((row, card))
    digests: list[PushNotification] = []
    for device_id, rows in by_device.items():
        titles = rules.TITLE_SEPARATOR.join(c.title for _, c in rows[:DIGEST_TITLES])
        title, body = rules.render(PushKind.DIGEST.value, {"n": str(len(rows)), "titles": titles})
        first = rows[0][0]
        digest = PushNotification(
            kind=PushKind.DIGEST.value,
            is_urgent=False,
            user_id=first.user_id,
            device_id=device_id,
            action_card_id=None,
            title=title,
            body=body,
            data={"kind": PushKind.DIGEST.value, "route": "/dashboard"},
            provider=first.provider,
            scheduled_for=now,
        )
        session.add(digest)
        digests.append(digest)
    session.flush()
    return digests


_hooks_registered = False


def register_hooks() -> None:
    """Закача пуша към `on_card_created`/`on_send_failed` (идемпотентно); куката пуска задача след commit."""
    global _hooks_registered
    if _hooks_registered:
        return
    from app.services import cards, outbound

    cards.register_on_card_created(_dispatch_card)
    outbound.register_on_send_failed(_dispatch_send_failed)
    _hooks_registered = True


def _dispatch_card(card_id: int) -> None:
    from app.workers.tasks import push as push_tasks

    push_tasks.enqueue_card.delay(card_id)


def _dispatch_send_failed(outbound_id: int) -> None:
    from app.workers.tasks import push as push_tasks

    push_tasks.enqueue_send_failed.delay(outbound_id)
