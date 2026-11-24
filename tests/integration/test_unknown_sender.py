"""Търсене на контрагенти и действия по карта unknown_sender (agro-br9.5)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import clock
from app.mail.matching import match_sender
from app.models.audit import AuditLog
from app.models.cards import ActionCard, CardKind, CardResolution, CardStatus
from app.models.mail import InboundEmail, InboundStatus, MatchMethod
from app.models.masterdata import Contact, ContactSource
from app.schemas.cards import UnknownSenderPayload
from app.services import unknown_sender
from app.services.cards import CardService
from tests.integration.test_trips import World
from tests.integration.test_trips_api import api, auth, w

pytestmark = pytest.mark.mail

__all__ = ["api", "auth", "w"]

SENDER = "Office@Agro-Fermer.bg"


@pytest.fixture
def dispatched(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    monkeypatch.setattr(unknown_sender, "dispatch_process_email", calls.append)
    return calls


def _card(w: World, suggested: int | None = None) -> tuple[ActionCard, InboundEmail]:
    email = InboundEmail(
        message_id="<us@x>",
        from_address=SENDER,
        subject="Заявка",
        received_at=clock.now(),
        raw_path="/tmp/x.eml",
        auth_ok=True,
        status=InboundStatus.NEEDS_ACTION.value,
    )
    w.session.add(email)
    w.session.flush()
    card = CardService.create(
        w.session,
        CardKind.UNKNOWN_SENDER,
        title="Имейл от непознат подател",
        subtitle="непознат подател",
        payload=UnknownSenderPayload(
            inbound_email_id=email.id,
            from_address=SENDER,
            subject="Заявка",
            suggested_counterparty_id=suggested,
            suggested_counterparty_name=None,
            suggestion_reason="domain" if suggested else None,
            auth_ok=True,
        ),
        inbound_email_id=email.id,
        dedupe_key=f"unknown_sender:{email.id}",
    )
    return card, email


def _act(api: TestClient, auth: dict[str, str], card: ActionCard, action: str, body: dict | None = None):  # type: ignore[type-arg]
    return api.post(f"/api/v1/cards/{card.id}/actions/{action}", json=body or {}, headers=auth)


def test_link_remember_creates_learned_contact_and_reprocesses(
    api: TestClient, auth: dict[str, str], w: World, dispatched: list[int]
) -> None:
    card, email = _card(w)
    r = _act(api, auth, card, "link", {"counterparty_id": w.client.id, "remember": True})
    assert r.status_code == 200, r.text
    assert r.json()["card"] is None

    w.session.refresh(email)
    w.session.refresh(card)
    contact = w.session.scalar(select(Contact).where(Contact.email == SENDER.lower()))
    assert contact is not None
    assert (contact.source, contact.counterparty_id) == (ContactSource.LEARNED.value, w.client.id)
    assert (email.counterparty_id, email.contact_id) == (w.client.id, contact.id)
    assert email.match_method == MatchMethod.MANUAL.value
    assert email.status == InboundStatus.NEW.value
    assert (card.status, card.resolution) == (CardStatus.DONE.value, CardResolution.LINKED.value)
    assert dispatched == [email.id]
    audited = w.session.scalar(
        select(AuditLog).where(AuditLog.entity_type == "contacts", AuditLog.entity_id == contact.id)
    )
    assert audited is not None


def test_next_mail_from_same_address_matches_by_contact(
    api: TestClient, auth: dict[str, str], w: World, dispatched: list[int]
) -> None:
    card, _ = _card(w)
    assert (
        _act(api, auth, card, "link", {"counterparty_id": w.client.id, "remember": True}).status_code == 200
    )
    again = InboundEmail(
        message_id="<us2@x>",
        from_address=SENDER.lower(),
        received_at=clock.now(),
        raw_path="/tmp/y.eml",
        auth_ok=True,
    )
    w.session.add(again)
    w.session.flush()
    match = match_sender(w.session, again)
    assert (match.counterparty_id, match.method) == (w.client.id, MatchMethod.CONTACT)


def test_link_without_remember_creates_no_contact(
    api: TestClient, auth: dict[str, str], w: World, dispatched: list[int]
) -> None:
    card, email = _card(w)
    assert _act(api, auth, card, "link", {"counterparty_id": w.client.id}).status_code == 200
    assert w.session.scalar(select(Contact).where(Contact.email == SENDER.lower())) is None
    w.session.refresh(email)
    assert (email.counterparty_id, email.contact_id, email.status) == (w.client.id, None, "new")
    assert dispatched == [email.id]


def test_link_unknown_counterparty_is_404_and_does_not_dispatch(
    api: TestClient, auth: dict[str, str], w: World, dispatched: list[int]
) -> None:
    card, email = _card(w)
    r = _act(api, auth, card, "link", {"counterparty_id": 999999, "remember": True})
    assert r.status_code == 404
    assert dispatched == []
    w.session.refresh(email)
    assert email.status == InboundStatus.NEEDS_ACTION.value


def test_link_requires_counterparty_id(api: TestClient, auth: dict[str, str], w: World) -> None:
    card, _ = _card(w)
    assert _act(api, auth, card, "link", {}).status_code == 400


def test_ignore(api: TestClient, auth: dict[str, str], w: World, dispatched: list[int]) -> None:
    card, email = _card(w)
    r = _act(api, auth, card, "ignore")
    assert r.status_code == 200, r.text
    w.session.refresh(email)
    w.session.refresh(card)
    assert email.status == InboundStatus.IGNORED.value
    assert (card.status, card.resolution) == (CardStatus.DISMISSED.value, CardResolution.IGNORED.value)
    assert dispatched == []


def test_search_labels_and_suggested_first(api: TestClient, auth: dict[str, str], w: World) -> None:
    w.client.name = "Агро Трейд"
    w.supplier.name = "Агро Пелет"
    w.session.flush()
    card, _ = _card(w, suggested=w.supplier.id)

    r = api.get("/api/v1/counterparties/search", params={"q": "агро"}, headers=auth)
    assert r.status_code == 200
    by_name = {i["name"]: i for i in r.json()}
    assert by_name["Агро Трейд"]["role_label"] == "Клиент · 1 договор"
    assert by_name["Агро Пелет"]["role_label"] == "Доставчик · 1 договор"
    assert not any(i["suggested"] for i in r.json())

    r = api.get("/api/v1/counterparties/search", params={"q": "агро", "card_id": card.id}, headers=auth)
    items = r.json()
    assert items[0]["name"] == "Агро Пелет"
    assert [i["suggested"] for i in items] == [True, False]


def test_search_both_roles_and_no_match(api: TestClient, auth: dict[str, str], w: World) -> None:
    w._contract("purchase", "П-2026-008", w.client, warehouse_id=w.wh.id)
    r = api.get("/api/v1/counterparties/search", params={"q": w.client.name}, headers=auth)
    assert r.json()[0]["role_label"] == "Клиент и доставчик"
    assert api.get("/api/v1/counterparties/search", params={"q": "няма-такъв"}, headers=auth).json() == []
    assert api.get("/api/v1/counterparties/search", params={"q": "%"}, headers=auth).json() == []


def test_search_requires_auth(api: TestClient) -> None:
    assert api.get("/api/v1/counterparties/search", params={"q": "а"}).status_code == 401
