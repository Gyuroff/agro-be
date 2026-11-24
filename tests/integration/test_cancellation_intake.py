"""Агент „откази“ (agro-6p5.6): писмо `cancellation` → карта (stub LLM)."""

from collections.abc import Callable
from datetime import date
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.audit_context import AuditContext
from app.models.cards import ActionCard, AgentRun, CardKind
from app.models.mail import InboundEmail, InboundStatus
from app.schemas.cards import CancellationPayload
from app.services import cancellation_intake
from tests.integration.test_cancellations import DAY, World
from tests.integration.test_report_intake import FakeModel

pytestmark = pytest.mark.mail


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> Callable[[dict[str, Any]], None]:
    def set_outputs(outputs: dict[str, Any]) -> None:
        monkeypatch.setattr("app.agents.llm.get_chat_model", lambda agent: FakeModel(agent, outputs))

    return set_outputs


@pytest.fixture
def w(db_session: Session, audit_ctx: AuditContext) -> World:
    return World(db_session)


def _mail(db: Session, w: World) -> InboundEmail:
    client_id = w.sale.counterparty_id
    mail = InboundEmail(
        message_id="<cancel-intake@x>",
        from_address="office@agrotrade.bg",
        subject="Отказ",
        body_text="Отказваме СВ 7890 ТР и РВ 1122 АВ за петък",
        received_at=clock.now(),
        raw_path="/tmp/x.eml",
        auth_ok=True,
        counterparty_id=client_id,
        kind="cancellation",
    )
    db.add(mail)
    db.flush()
    return mail


def _card(db: Session, mail: InboundEmail) -> ActionCard:
    return db.scalars(select(ActionCard).where(ActionCard.inbound_email_id == mail.id)).one()


def test_two_trips_in_card(db_session: Session, w: World, stub: Callable[[dict[str, Any]], None]) -> None:
    t1, t2 = w.trip("СВ 7890 ТР"), w.trip("РВ 1122 АВ")
    stub({"cancellation_parser": {"date": DAY.isoformat(), "plates": ["СВ 7890 ТР", "РВ 1122 АВ"]}})
    mail = _mail(db_session, w)

    assert cancellation_intake.handle(db_session, mail) is InboundStatus.NEEDS_ACTION

    card = _card(db_session, mail)
    assert card.kind == CardKind.CANCELLATION.value
    payload = CancellationPayload.model_validate(card.payload)
    assert payload.trip_ids == [t1.id, t2.id]
    assert payload.date == DAY and payload.notify_supplier is True
    assert payload.plates == ["СВ 7890 ТР", "РВ 1122 АВ"] and payload.not_found_plates == []
    assert payload.supplier_name == "Пелет АД"
    assert "2 курса" in card.title
    run = db_session.scalars(select(AgentRun).where(AgentRun.agent == "cancellation_parser")).one()
    assert run.status == "ok" and run.subject_id == mail.id


def test_unknown_plate_is_listed_and_card_still_created(
    db_session: Session, w: World, stub: Callable[[dict[str, Any]], None]
) -> None:
    trip = w.trip("СВ 7890 ТР")
    stub({"cancellation_parser": {"date": DAY.isoformat(), "plates": ["св7890тр", "ОУ 0001 ХХ"]}})
    mail = _mail(db_session, w)

    cancellation_intake.handle(db_session, mail)

    payload = CancellationPayload.model_validate(_card(db_session, mail).payload)
    assert payload.trip_ids == [trip.id]
    assert payload.not_found_plates == ["ОУ 0001 ХХ"]


def test_nothing_found_still_creates_card(
    db_session: Session, w: World, stub: Callable[[dict[str, Any]], None]
) -> None:
    stub({"cancellation_parser": {"plates": ["СВ 7890 ТР"]}})
    mail = _mail(db_session, w)

    cancellation_intake.handle(db_session, mail)

    payload = CancellationPayload.model_validate(_card(db_session, mail).payload)
    assert payload.trip_ids == [] and payload.not_found_plates == ["СВ 7890 ТР"]
    assert payload.date == mail.received_at.date() and payload.supplier_name == "—"


def test_date_window_is_one_day_and_only_planned(
    db_session: Session, w: World, stub: Callable[[dict[str, Any]], None]
) -> None:
    w.trip("СВ 7890 ТР")  # планиран на DAY
    stub({"cancellation_parser": {"date": date(2026, 11, 15).isoformat(), "plates": ["СВ 7890 ТР"]}})
    mail = _mail(db_session, w)

    cancellation_intake.handle(db_session, mail)

    payload = CancellationPayload.model_validate(_card(db_session, mail).payload)
    assert payload.trip_ids == [] and payload.not_found_plates == ["СВ 7890 ТР"]  # DAY е на 2 дни


def test_other_customers_trip_is_not_found(
    db_session: Session, w: World, stub: Callable[[dict[str, Any]], None]
) -> None:
    trip = w.trip("СВ 7890 ТР")
    trip.customer_id = w.purchase.counterparty_id  # курсът е на друг клиент
    db_session.flush()
    stub({"cancellation_parser": {"date": DAY.isoformat(), "plates": ["СВ 7890 ТР"]}})
    mail = _mail(db_session, w)

    cancellation_intake.handle(db_session, mail)

    assert CancellationPayload.model_validate(_card(db_session, mail).payload).trip_ids == []
