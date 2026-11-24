"""Пуш: опашка, тихи часове, затворена карта, обобщение, мъртъв токен, send_failed (agro-zvb.1)."""

from datetime import UTC, datetime

import pytest
from freezegun import freeze_time
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit_context import AuditContext
from app.models.cards import (
    ActionCard,
    CardKind,
    CardResolution,
    CardStatus,
    PushKind,
    PushNotification,
)
from app.models.identity import Device, User
from app.models.mail import OutboundPurpose
from app.push.sender import InvalidToken, PushMessage
from app.services import push
from app.services.cards import CardService
from app.services.outbound import OutboundService
from app.services.users import UserService


def sofia(hour: int, minute: int = 0, day: int = 16) -> datetime:
    return datetime(2026, 11, day, hour - 2, minute, tzinfo=UTC)  # ноември: UTC+2


class FakeSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, PushMessage]] = []
        self.dead: set[str] = set()

    def send(self, token: str, message: PushMessage) -> str:
        if token in self.dead:
            raise InvalidToken("UNREGISTERED")
        self.sent.append((token, message))
        return f"msg-{len(self.sent)}"


@pytest.fixture
def sender() -> FakeSender:
    return FakeSender()


@pytest.fixture
def device(db_session: Session, audit_ctx: AuditContext) -> Device:
    user = UserService.create(
        db_session, email="p@agroimport.bg", full_name="П П", short_name="П", password="tajna-parola-1"
    )
    dev = Device(
        user_id=user.id, installation_id="i-1", platform="android", push_provider="fcm", push_token="tok-1"
    )
    db_session.add(dev)
    db_session.flush()
    return dev


def over_card(session: Session, n: int = 1) -> ActionCard:
    return CardService.create(
        session,
        CardKind.CONTRACT_OVER,
        title=f"Договорът {n} не стига",
        payload={
            "contract_id": n,
            "contract_number": f"2026-02{n}",
            "product": "Пелети",
            "remaining_t": "10.000",
            "requested_t": "20.000",
            "shortage_t": "10.000",
        },
        dedupe_key=f"contract_over:{n}",
    )


def cancel_card(session: Session) -> ActionCard:
    return CardService.create(
        session,
        CardKind.CANCELLATION,
        title="Отказани курсове",
        payload={
            "trip_ids": [1, 2],
            "date": "2026-11-13",
            "plates": ["СВ 7890 ТР", "РВ 1122 АВ"],
            "supplier_name": "Пелет АД",
            "notify_supplier": True,
        },
    )


def rows(session: Session) -> list[PushNotification]:
    return list(session.scalars(select(PushNotification).order_by(PushNotification.id)))


def test_daytime_card_is_sent_now(db_session: Session, device: Device, sender: FakeSender) -> None:
    with freeze_time(sofia(10)):
        card = over_card(db_session)
        (row,) = push.enqueue_for_card(db_session, card.id)
        assert row.scheduled_for == sofia(10) and row.status == "queued"
        assert push.dispatch_due(db_session, {"fcm": sender})["sent"] == 1
    assert row.status == "sent" and row.provider_message_id == "msg-1"
    token, message = sender.sent[0]
    assert token == "tok-1"
    assert message.title == "Договорът с  не стига" and message.body == "№ 2026-021: не стигат 10,00 т"
    assert message.data == {
        "kind": "contract_over",
        "route": f"/dashboard?card={card.id}",
        "card_id": str(card.id),
    }
    assert message.collapse_key == f"card-{card.id}" and not message.is_urgent
    db_session.refresh(card)
    assert card.push_sent_at == sofia(10)
    assert push.enqueue_for_card(db_session, card.id) == []  # без дубли


def test_night_non_urgent_is_deferred_urgent_goes_now(
    db_session: Session, device: Device, sender: FakeSender
) -> None:
    with freeze_time(sofia(23)):
        normal = push.enqueue_for_card(db_session, over_card(db_session).id)[0]
        urgent = push.enqueue_for_card(db_session, cancel_card(db_session).id)[0]
        assert normal.scheduled_for == sofia(7, day=17)
        assert urgent.scheduled_for == sofia(23) and urgent.is_urgent
        counts = push.dispatch_due(db_session, {"fcm": sender})
    assert counts["sent"] == 1 and normal.status == "queued" and urgent.status == "sent"
    assert sender.sent[0][1].is_urgent and sender.sent[0][1].title == "Отказани 2 курса от Пелет АД"


def test_card_closed_before_dispatch_is_skipped(
    db_session: Session, device: Device, sender: FakeSender
) -> None:
    with freeze_time(sofia(10)):
        card = over_card(db_session)
        (row,) = push.enqueue_for_card(db_session, card.id)
        CardService.resolve(db_session, card, CardStatus.DONE, CardResolution.CONFIRMED)
        assert push.dispatch_due(db_session, {"fcm": sender})["skipped"] == 1
    assert row.status == "skipped" and sender.sent == []
    db_session.refresh(card)
    assert card.push_sent_at is None


def test_three_night_cards_become_one_digest_at_seven(
    db_session: Session, device: Device, sender: FakeSender
) -> None:
    with freeze_time(sofia(23)):
        cards = [over_card(db_session, n) for n in (1, 2, 3)]
        for card in cards:
            push.enqueue_for_card(db_session, card.id)
    with freeze_time(sofia(7, 0, day=17)):
        assert push.dispatch_due(db_session, {"fcm": sender}) == {
            "queued": 0,
            "sent": 0,
            "failed": 0,
            "skipped": 0,
        }  # поединично не излизат — чакат обобщението
        digests = push.morning_digest(db_session)
        assert len(digests) == 1
        push.dispatch_due(db_session, {"fcm": sender})
    assert len(sender.sent) == 1
    message = sender.sent[0][1]
    assert message.title == "3 действия чакат"
    assert all(f"Договорът {n} не стига" in message.body for n in (1, 2, 3))
    night = [r for r in rows(db_session) if r.kind == "contract_over"]
    assert [r.status for r in night] == ["skipped"] * 3
    assert digests[0].status == "sent" and digests[0].action_card_id is None
    assert message.collapse_key is None


def test_unregistered_token_is_cleared(db_session: Session, device: Device, sender: FakeSender) -> None:
    sender.dead.add("tok-1")
    with freeze_time(sofia(10)):
        (row,) = push.enqueue_for_card(db_session, over_card(db_session).id)
        assert push.dispatch_due(db_session, {"fcm": sender})["failed"] == 1
    db_session.refresh(device)
    assert row.status == "failed" and row.error and "UNREGISTERED" in row.error
    assert device.push_token is None and device.push_provider == "none"
    assert push.enqueue_for_card(db_session, over_card(db_session, 2).id) == []  # вече няма получател


def test_recipients_exclude_disabled_revoked_and_tokenless(db_session: Session, device: Device) -> None:
    user = db_session.get_one(User, device.user_id)
    with freeze_time(sofia(10)):
        user.push_enabled = False
        db_session.flush()
        assert push.enqueue_for_card(db_session, over_card(db_session).id) == []
        user.push_enabled = True
        device.revoked_at = sofia(9)
        db_session.flush()
        assert push.enqueue_for_card(db_session, over_card(db_session, 2).id) == []


def test_send_failed_hook_pushes_urgent_p4(db_session: Session, device: Device, sender: FakeSender) -> None:
    out = OutboundService.create_draft(
        db_session,
        purpose=OutboundPurpose.SUPPLIER_ORDER,
        to_addresses=["orders@peletad.bg"],
        subject="Заявка",
        body_text="x",
    )
    card = over_card(db_session)
    card.outbound_email_id = out.id
    db_session.flush()
    with freeze_time(sofia(23)):
        (row,) = push.enqueue_send_failed(db_session, out.id)
        assert row.is_urgent and row.kind == PushKind.SEND_FAILED.value
        push.dispatch_due(db_session, {"fcm": sender})
    assert row.status == "sent"
    assert row.title == "Имейлът до orders@peletad.bg не е изпратен"
    assert row.data["route"] == f"/dashboard?card={card.id}"


def test_register_hooks_wires_both_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import cards, outbound

    monkeypatch.setattr(push, "_hooks_registered", False)
    before = (len(cards._created_hooks), len(outbound._failed_hooks))  # pyright: ignore[reportPrivateUsage]
    push.register_hooks()
    push.register_hooks()  # идемпотентно
    assert (len(cards._created_hooks), len(outbound._failed_hooks)) == (  # pyright: ignore[reportPrivateUsage]
        before[0] + 1,
        before[1] + 1,
    )
    cards._created_hooks.pop()  # pyright: ignore[reportPrivateUsage]
    outbound._failed_hooks.pop()  # pyright: ignore[reportPrivateUsage]
