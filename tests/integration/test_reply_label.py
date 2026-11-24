"""Етикет „отговор“ (agro-nfi): reply в GET /outbound-emails/{id}, CardDTO и RequestDTO."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core import clock
from app.core.audit_context import AuditContext
from app.main import create_app
from app.models.mail import EmailKind, InboundEmail, OutboundEmail, OutboundPurpose, OutboundStatus
from app.services.reply_label import reply_label
from app.services.users import UserService
from tests.integration.test_auth import EMAIL, PASSWORD, bearer, login
from tests.integration.test_trips import World


@pytest.fixture
def w(db_session: Session, audit_ctx: AuditContext) -> World:
    return World(db_session)


@pytest.fixture
def api(db_session: Session, w: World) -> Iterator[TestClient]:
    UserService.create(
        db_session, email=EMAIL, full_name="Георги Гюров", short_name="Георги", password=PASSWORD
    )
    db_session.flush()
    app = create_app()

    def override_db() -> Iterator[Session]:
        db_session.commit()
        yield db_session
        db_session.commit()

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        yield client


def outbound(w: World, session: Session, *, sent: bool = True) -> OutboundEmail:
    mail = OutboundEmail(
        reference_code=f"АИТ-{uuid.uuid4().hex[:8]}",
        purpose=OutboundPurpose.SUPPLIER_ORDER.value,
        counterparty_id=w.supplier.id,
        to_addresses=["orders@supplier.test"],
        subject="Заявка",
        body_text="Текст",
        status=OutboundStatus.SENT.value if sent else OutboundStatus.DRAFT.value,
        sent_at=clock.now() if sent else None,
    )
    session.add(mail)
    session.flush()
    return mail


def reply(session: Session, out: OutboundEmail, kind: str, status: str | None) -> InboundEmail:
    mail = InboundEmail(
        message_id=f"<r-{uuid.uuid4().hex}@x>",
        from_address="orders@supplier.test",
        received_at=clock.now(),
        raw_path="/tmp/x.eml",
        auth_ok=True,
        kind=kind,
        reply_status=status,
        replies_to_outbound_id=out.id,
    )
    session.add(mail)
    session.flush()
    return mail


def test_awaiting_then_confirmed_then_needs_attention(w: World, db_session: Session) -> None:
    out = outbound(w, db_session)
    label = reply_label(db_session, out.id)
    assert label is not None
    assert (label.status, label.count, label.inbound_email_id, label.received_at) == (
        "awaiting",
        0,
        None,
        None,
    )
    assert label.counterparty_short_name == w.supplier.name

    ok = reply(db_session, out, EmailKind.REPLY.value, "confirmed")
    label = reply_label(db_session, out.id)
    assert label is not None
    assert (label.status, label.count, label.inbound_email_id) == ("confirmed", 1, ok.id)

    other = reply(db_session, out, EmailKind.OTHER.value, None)
    label = reply_label(db_session, out.id)
    assert label is not None
    assert (label.status, label.count, label.inbound_email_id) == ("needs_attention", 2, other.id)


def test_null_without_outbound_or_unsent(w: World, db_session: Session) -> None:
    assert reply_label(db_session, None) is None
    assert reply_label(db_session, outbound(w, db_session, sent=False).id) is None


def test_outbound_route_has_reply_and_inbound_route_does_not_see_it(
    api: TestClient, w: World, db_session: Session
) -> None:
    out = outbound(w, db_session)
    reply(db_session, out, EmailKind.REPLY.value, "confirmed")
    auth = bearer(login(api).json()["access_token"])
    body = api.get(f"/api/v1/outbound-emails/{out.id}", headers=auth).json()
    assert body["subject"] == "Заявка"
    assert body["to"] == ["orders@supplier.test"]
    assert body["reply"]["status"] == "confirmed"
    assert body["reply"]["sent_at"].endswith("Z")
    assert api.get("/api/v1/outbound-emails/999999", headers=auth).status_code == 404
