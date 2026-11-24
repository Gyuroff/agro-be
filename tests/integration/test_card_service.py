"""CardService, действия по карти и GET /dashboard (agro-br9.1)."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core import clock
from app.core.audit_context import AuditContext
from app.core.errors import DomainError
from app.main import create_app
from app.models.cards import ActionCard, CardKind, CardResolution, CardStatus
from app.models.identity import Actor
from app.models.mail import MailboxPoll, PollStatus
from app.models.trips import CancelSource, Trip
from app.schemas.cards import ContractOverPayload, MailboxAlarmPayload
from app.services.cards import (
    ActionResult,
    CardService,
    register_action,
    register_on_card_created,
)
from app.services.trips import TripService
from app.services.users import UserService
from tests.integration.test_auth import EMAIL, PASSWORD, bearer, login
from tests.integration.test_trips import World

D = Decimal


def over_payload(**kw: object) -> ContractOverPayload:
    data = dict(  # noqa: C408
        contract_id=4,
        contract_number="2026-021",
        product="Пелети",
        remaining_t=D("236.400"),
        requested_t=D("302.400"),
        shortage_t=D("66.000"),
    )
    return ContractOverPayload(**{**data, **kw})  # type: ignore[arg-type]


def make_over(session: Session, key: str | None = "contract_over:4") -> ActionCard:
    return CardService.create(
        session,
        CardKind.CONTRACT_OVER,
        title="Договорът с Агро Трейд не стига",
        payload=over_payload(),
        dedupe_key=key,
    )


# ---- услугата ----


def test_create_validates_payload(db_session: Session, audit_ctx: AuditContext) -> None:
    card = make_over(db_session)
    assert card.status == "open"
    assert card.payload["shortage_t"] == "66.000"  # пари/тонове в JSON са низове
    with pytest.raises(ValueError):
        CardService.create(
            db_session, CardKind.CONTRACT_OVER, title="x", payload={"contract_id": 1, "непознато": 1}
        )


def test_dedupe_returns_same_open_card(db_session: Session, audit_ctx: AuditContext) -> None:
    first = make_over(db_session)
    second, created = CardService.create_or_get(
        db_session,
        CardKind.CONTRACT_OVER,
        title="друго",
        payload=over_payload(),
        dedupe_key="contract_over:4",
    )
    assert second.id == first.id and created is False
    assert db_session.scalars(select(ActionCard).where(ActionCard.dedupe_key == "contract_over:4")).all() == [
        first
    ]


def test_dedupe_after_close_creates_new(db_session: Session, audit_ctx: AuditContext) -> None:
    first = make_over(db_session)
    CardService.resolve(db_session, first, CardStatus.DONE, CardResolution.CONFIRMED)
    assert make_over(db_session).id != first.id


def test_resolve_sets_actor_and_time(db_session: Session, audit_ctx: AuditContext) -> None:
    card = make_over(db_session)
    CardService.resolve(db_session, card, CardStatus.DISMISSED, CardResolution.HIDDEN)
    assert (card.status, card.resolution, card.resolved_by_id) == ("dismissed", "hidden", audit_ctx.actor_id)
    assert card.resolved_at is not None
    with pytest.raises(DomainError) as exc:
        CardService.resolve(db_session, card, CardStatus.DONE, CardResolution.SENT)
    assert exc.value.code == "card_not_open"


def test_on_card_created_runs_after_commit_only_for_new(
    db_session: Session, audit_ctx: AuditContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int] = []
    monkeypatch.setattr("app.services.cards._created_hooks", [seen.append])
    card = make_over(db_session)
    make_over(db_session)  # dedupe → не е нова
    assert seen == []  # още няма commit
    db_session.commit()
    assert seen == [card.id]
    make_over(db_session)
    db_session.commit()
    assert seen == [card.id]


def test_hook_not_called_after_rollback(
    db_session: Session, audit_ctx: AuditContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int] = []
    monkeypatch.setattr("app.services.cards._created_hooks", [seen.append])
    make_over(db_session)
    db_session.rollback()
    db_session.commit()
    assert seen == []


def test_hook_failure_does_not_break_commit(
    db_session: Session, audit_ctx: AuditContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_: int) -> None:
        raise RuntimeError("fcm down")

    monkeypatch.setattr("app.services.cards._created_hooks", [boom])
    card = make_over(db_session)
    db_session.commit()
    assert db_session.get(ActionCard, card.id) is not None


def test_register_on_card_created_adds_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    hooks: list = []  # type: ignore[type-arg]
    monkeypatch.setattr("app.services.cards._created_hooks", hooks)
    register_on_card_created(lambda _: None)
    assert len(hooks) == 1


def test_mailbox_alarm_goes_through_card_service(db_session: Session, audit_ctx: AuditContext) -> None:
    payload = MailboxAlarmPayload(last_ok_at=None, minutes=75)
    card = CardService.create(
        db_session, CardKind.MAILBOX_ALARM, title="Пощата", payload=payload, dedupe_key="mailbox_alarm"
    )
    assert card.payload == {"last_ok_at": None, "minutes": 75}


# ---- API ----


@pytest.fixture
def api(db_session: Session, audit_ctx: AuditContext) -> Iterator[TestClient]:
    UserService.create(
        db_session, email=EMAIL, full_name="Георги Гюров", short_name="Георги", password=PASSWORD
    )
    db_session.flush()
    app = create_app()

    def override_db() -> Iterator[Session]:
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        yield client


@pytest.fixture
def auth(api: TestClient) -> dict[str, str]:
    return bearer(login(api).json()["access_token"])


def test_hide_dismisses_and_records_actor(
    api: TestClient, auth: dict[str, str], db_session: Session, audit_ctx: AuditContext
) -> None:
    card = make_over(db_session)
    response = api.post(f"/api/v1/cards/{card.id}/actions/hide", headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert body["card"] is None and body["undo_token"] is None and body["snackbar"]
    db_session.refresh(card)
    assert (card.status, card.resolution) == ("dismissed", "hidden")
    actor = db_session.scalar(select(Actor).where(Actor.id == card.resolved_by_id))
    assert actor is not None and actor.code.startswith("user:")  # потребителят, не seed


def test_action_on_closed_card_is_409(api: TestClient, auth: dict[str, str], db_session: Session) -> None:
    card = make_over(db_session)
    assert api.post(f"/api/v1/cards/{card.id}/actions/hide", headers=auth).status_code == 200
    response = api.post(f"/api/v1/cards/{card.id}/actions/hide", headers=auth)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "card_not_open"


def test_unknown_action_and_card_are_404(api: TestClient, auth: dict[str, str], db_session: Session) -> None:
    card = make_over(db_session)
    assert api.post(f"/api/v1/cards/{card.id}/actions/send", headers=auth).status_code == 404
    assert api.post("/api/v1/cards/999999/actions/hide", headers=auth).status_code == 404


def test_actions_require_login(api: TestClient, db_session: Session) -> None:
    card = make_over(db_session)
    assert api.post(f"/api/v1/cards/{card.id}/actions/hide").status_code == 401
    assert api.get("/api/v1/dashboard").status_code == 401


def test_registered_handler_gets_body_and_can_return_undo(
    api: TestClient, auth: dict[str, str], db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []  # type: ignore[type-arg]

    def handler(session: Session, card: ActionCard, body: dict) -> ActionResult:  # type: ignore[type-arg]
        calls.append(body)
        return ActionResult("Готово", undo_token="u_1")

    monkeypatch.setattr("app.services.cards._handlers", {(CardKind.CONTRACT_OVER, "poke"): handler})
    card = make_over(db_session)
    response = api.post(f"/api/v1/cards/{card.id}/actions/poke", headers=auth, json={"x": 1})
    assert response.status_code == 200
    body = response.json()
    assert calls == [{"x": 1}]
    assert body["undo_token"] == "u_1" and body["card"]["id"] == card.id  # картата още е отворена


def test_register_action_rejects_duplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.cards._handlers", {})
    register_action(CardKind.INQUIRY, "ignore", lambda *_: ActionResult("ok"))
    with pytest.raises(RuntimeError):
        register_action(CardKind.INQUIRY, "ignore", lambda *_: ActionResult("ok"))


def test_dashboard_shape(
    api: TestClient, auth: dict[str, str], db_session: Session, audit_ctx: AuditContext
) -> None:
    w = World(db_session)
    today = clock.today_sofia()
    trips: list[Trip] = []
    for i in range(8):
        t = w.planned(plate=f"СА 10{i}0 ВХ")
        t.planned_date = today
        trips.append(t)
    TripService(db_session).cancel(trips[7], source=CancelSource.USER, reason="по телефона")
    other_day = w.planned(plate="СА 9999 ВХ")  # 2026-02-10 — не е днес
    assert other_day.planned_date != today
    ok_at = clock.now() - timedelta(minutes=2)
    db_session.add(
        MailboxPoll(
            started_at=clock.now() - timedelta(minutes=3),
            finished_at=ok_at,
            status=PollStatus.OK.value,
        )
    )
    db_session.add(
        MailboxPoll(started_at=clock.now(), finished_at=clock.now(), status=PollStatus.ERROR.value, error="x")
    )
    old = make_over(db_session, "contract_over:1")
    old.created_at = clock.now() - timedelta(hours=2)
    newest = make_over(db_session, "contract_over:2")
    closed = make_over(db_session, "contract_over:3")
    CardService.resolve(db_session, closed, CardStatus.DONE, CardResolution.CONFIRMED)
    db_session.flush()

    response = api.get("/api/v1/dashboard", headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert body["today"] == today.isoformat()
    assert body["last_mail_check_at"] == ok_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    assert [c["id"] for c in body["cards"]] == [newest.id, old.id]  # само open, най-новите първи
    assert body["cards"][0].keys() == {
        "id",
        "kind",
        "title",
        "subtitle",
        "created_at",
        "call_phone",
        "payload",
        "reply",
    }
    assert body["cards"][0]["reply"] is None
    today_block = body["trips_today"]
    assert today_block["count"] == 7  # без отказания и без другия ден
    assert today_block["planned_qty_t"] == "176.400"
    assert len(today_block["items"]) == 6
    first = today_block["items"][0]
    assert first == {
        "id": trips[0].id,
        "plate": "СА 1000 ВХ",
        "product": "Пелети",
        "route": "Склад Агро Сует, Тервел → Агро Трейд",
        "date": today.isoformat(),
        "status": "planned",
        "actual_qty_t": None,
        "planned_qty_t": "25.200",
    }


def test_dashboard_empty(api: TestClient, auth: dict[str, str]) -> None:
    body = api.get("/api/v1/dashboard", headers=auth).json()
    assert body["cards"] == [] and body["last_mail_check_at"] is None
    assert body["trips_today"] == {"count": 0, "planned_qty_t": "0", "items": []}


def test_mailbox_alarm_can_be_hidden(
    api: TestClient, auth: dict[str, str], db_session: Session, audit_ctx: AuditContext
) -> None:
    card, _ = CardService.create_or_get(
        db_session,
        CardKind.MAILBOX_ALARM,
        title="Пощата мълчи",
        payload=MailboxAlarmPayload(last_ok_at=datetime(2026, 11, 12, 8, 43, tzinfo=UTC), minutes=75),
        dedupe_key="mailbox_alarm",
    )
    db_session.flush()
    assert api.post(f"/api/v1/cards/{card.id}/actions/hide", headers=auth).status_code == 200
    db_session.refresh(card)
    assert (card.status, card.resolution) == ("dismissed", "hidden")
