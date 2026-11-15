"""action_cards, agent_runs, push_notifications (agro-l2v.9)."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import clock
from app.core.audit_context import AuditContext
from app.models.audit import AuditLog
from app.models.cards import (
    ActionCard,
    AgentRun,
    CardKind,
    CardResolution,
    CardStatus,
    PushKind,
    PushNotification,
)
from app.models.identity import Device
from app.services.users import UserService


def _card(dedupe_key: str | None, status: str = "open") -> ActionCard:
    return ActionCard(
        kind=CardKind.CONTRACT_OVER.value, title="Договорът не стига", dedupe_key=dedupe_key, status=status
    )


def test_two_open_cards_same_dedupe_key_rejected(db_session: Session, audit_ctx: AuditContext) -> None:
    db_session.add(_card("contract_over:42"))
    db_session.flush()
    with db_session.begin_nested():
        db_session.add(_card("contract_over:42"))
        with pytest.raises(IntegrityError):
            db_session.flush()


def test_closed_and_open_card_same_dedupe_key_allowed(db_session: Session, audit_ctx: AuditContext) -> None:
    db_session.add_all([_card("contract_over:42", CardStatus.DONE.value), _card("contract_over:42")])
    db_session.flush()
    db_session.add(_card("contract_over:42", CardStatus.DISMISSED.value))
    db_session.flush()


def test_cards_without_dedupe_key_do_not_collide(db_session: Session, audit_ctx: AuditContext) -> None:
    db_session.add_all([_card(None), _card(None)])
    db_session.flush()


def test_card_defaults_and_checks(db_session: Session, audit_ctx: AuditContext) -> None:
    card = _card(None)
    db_session.add(card)
    db_session.flush()
    db_session.refresh(card)
    assert card.payload == {}
    assert card.resolution is None
    with db_session.begin_nested():
        db_session.add(ActionCard(kind="nope", title="x"))
        with pytest.raises(IntegrityError):
            db_session.flush()
    with db_session.begin_nested():
        db_session.add(
            ActionCard(kind=CardKind.INQUIRY.value, title="x", resolution="whatever"),
        )
        with pytest.raises(IntegrityError):
            db_session.flush()
    card.resolution = CardResolution.HIDDEN.value
    db_session.flush()


def test_cards_are_audited_but_agent_runs_and_push_are_not(
    db_session: Session, audit_ctx: AuditContext
) -> None:
    user = UserService.create(
        db_session, email="a@agro.bg", full_name="A", short_name="A", password="pw-pw-pw-1"
    )
    device = Device(user_id=user.id, installation_id="i-1", platform="ios")
    card = _card("contract_over:1")
    run = AgentRun(agent="request_parser", status="running")
    db_session.add_all([device, card, run])
    db_session.flush()
    push = PushNotification(
        kind=PushKind.DIGEST.value,
        user_id=user.id,
        device_id=device.id,
        title="2 действия чакат",
        body="…",
        provider="fcm",
        scheduled_for=clock.now(),
    )
    db_session.add(push)
    db_session.flush()
    db_session.refresh(push)
    assert push.status == "queued"
    assert push.action_card_id is None  # digest
    tables = set(db_session.scalars(select(AuditLog.entity_type)))
    assert "action_cards" in tables
    assert not tables & {"agent_runs", "push_notifications"}
    with db_session.begin_nested():
        db_session.add(AgentRun(agent="unknown", status="ok"))
        with pytest.raises(IntegrityError):
            db_session.flush()
