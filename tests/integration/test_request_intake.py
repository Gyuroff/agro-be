"""Агент „заявки“ (agro-6p5.4): писмо `request` → заявка + редове + карта `client_request`."""

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.audit_context import AuditContext
from app.models.cards import ActionCard, CardKind
from app.models.contracts import Contract
from app.models.mail import InboundEmail, InboundStatus
from app.models.masterdata import Contact, CounterpartyDomain, Warehouse
from app.models.trips import ClientRequest, ClientRequestLine
from app.services import inbound_mail
from app.services.request_intake import pick_contract
from tests.integration.test_trips import World

D = Decimal


class FakeModel:
    """Връща готов изход за агента: класификаторът → `request`, парсерът → `parsed`."""

    def __init__(self, agent: str, parsed: dict[str, Any]) -> None:
        self.agent, self.parsed = agent, parsed

    def with_structured_output(self, schema: type[BaseModel], **_: Any) -> Runnable[Any, Any]:
        data = (
            {"kind": "request", "confidence": 0.95, "reason": "заявка с таблица"}
            if self.agent == "email_classifier"
            else self.parsed
        )
        return RunnableLambda(lambda _m: schema.model_validate(data))


@pytest.fixture
def parse(monkeypatch: pytest.MonkeyPatch) -> Callable[[dict[str, Any]], None]:
    def set_output(parsed: dict[str, Any]) -> None:
        monkeypatch.setattr("app.agents.llm.get_chat_model", lambda agent: FakeModel(agent, parsed))

    return set_output


def _line(day: Any, plate: str, **kw: Any) -> dict[str, Any]:
    return {"date": day.isoformat(), "plate_raw": plate, **kw}


def _mail(db: Session, w: World) -> InboundEmail:
    db.add(CounterpartyDomain(counterparty_id=w.client.id, domain="agrotrade.bg"))
    contact = Contact(counterparty_id=w.client.id, email="maria@agrotrade.bg", phone="+359888123456")
    db.add(contact)
    db.flush()
    mail = InboundEmail(
        message_id="<intake@x>",
        from_address="maria@agrotrade.bg",
        subject="Курсове 19.11–25.11",
        body_text="Изпращам камионите за седмицата.",
        received_at=clock.now(),
        raw_path="/tmp/x.eml",
        auth_ok=True,
    )
    db.add(mail)
    db.flush()
    return mail


def _world(db: Session) -> World:
    w = World(db)
    contact = Contact(counterparty_id=w.supplier.id, email="orders@sup.test", receives_orders=True)
    db.add(contact)
    db.flush()
    w.purchase.order_contact_id = contact.id
    w.sale.supply_contract_id = w.purchase.id
    w.sale.planned_truck_qty_t = D("25.200")
    db.flush()
    return w


def test_figma_request_12_trucks(
    db_session: Session, audit_ctx: AuditContext, parse: Callable[[dict[str, Any]], None]
) -> None:
    w = _world(db_session)
    mail = _mail(db_session, w)
    start = clock.today_sofia() + timedelta(days=3)
    plates = [f"СА {1000 + i} ВХ" for i in range(12)]
    lines = [_line(start + timedelta(days=i % 7), p, driver_name=f"Шофьор {i}") for i, p in enumerate(plates)]
    lines[3] = _line(
        start, plates[3], driver_name="Иван ?", uncertain_fields=["driver"], uncertain_note="нечетливо"
    )
    parse(
        {
            "period_from": start.isoformat(),
            "period_to": (start + timedelta(days=6)).isoformat(),
            "lines": lines,
        }
    )

    assert inbound_mail.process(db_session, mail.id) is InboundStatus.NEEDS_ACTION

    request = db_session.scalars(select(ClientRequest)).one()
    rows = list(
        db_session.scalars(select(ClientRequestLine).where(ClientRequestLine.request_id == request.id))
    )
    assert request.status == "draft" and request.sale_contract_id == w.sale.id
    assert request.outbound_email_id is None and request.agent_run_id is not None
    assert len(rows) == 12 and {r.planned_qty_t for r in rows} == {D("25.200")}
    assert [r.line_no for r in rows] == list(range(1, 13))
    card = db_session.scalars(select(ActionCard)).one()
    assert card.kind == CardKind.CLIENT_REQUEST.value and card.client_request_id == request.id
    assert card.payload["total_trucks"] == 12 and card.payload["total_qty_t"] == "302.400"
    assert card.payload["has_uncertain"] is True and card.payload["to"] == "orders@sup.test"
    assert sum(1 for x in card.payload["lines"] if x["uncertain"]) == 1
    assert card.call_phone == "+359888123456" and card.counterparty_id == w.client.id
    labels = {n["label"]: n for n in request.agent_notes or []}
    assert {"Вид", "Клиент", "Договор", "Период", "Камиони"} <= set(labels)
    assert labels["Договор"]["value"] == "2026-018"
    assert any(k.startswith("Несигурно") for k in labels)


def test_invalid_plate_and_past_date_are_uncertain(
    db_session: Session, audit_ctx: AuditContext, parse: Callable[[dict[str, Any]], None]
) -> None:
    w = _world(db_session)
    mail = _mail(db_session, w)
    today = clock.today_sofia()
    parse(
        {
            "lines": [
                _line(today + timedelta(days=2), "ХХ12"),
                _line(today - timedelta(days=1), "СА 1234 ВХ", qty_t="20.000"),
            ]
        }
    )
    inbound_mail.process(db_session, mail.id)
    bad, past = db_session.scalars(select(ClientRequestLine).order_by(ClientRequestLine.line_no))
    assert bad.uncertain_fields == ["plate"] and "невалиден номер" in (bad.uncertain_note or "")
    assert past.uncertain_fields == ["date"] and past.planned_qty_t == D("20.000")
    assert bad.planned_qty_t == D("25.200")


def _second_contract(db: Session, w: World) -> Contract:
    other_wh = Warehouse(counterparty_id=w.supplier.id, name="Варна")
    db.add(other_wh)
    db.flush()
    purchase2 = w._contract("purchase", "П-2026-008", w.supplier, warehouse_id=other_wh.id)
    sale2 = w._contract("sale", "2026-019", w.client, supply_contract_id=purchase2.id)
    return sale2


def test_two_contracts_warehouse_hint_picks_right_one(db_session: Session, audit_ctx: AuditContext) -> None:
    w = _world(db_session)
    sale2 = _second_contract(db_session, w)
    assert pick_contract(db_session, w.client.id, None, "Варна") is sale2
    assert pick_contract(db_session, w.client.id, "Пелети", "Тервел") is w.sale
    assert pick_contract(db_session, w.client.id, None, None) is None
    assert pick_contract(db_session, w.client.id, None, "Бургас") is None  # подсказка без съвпадение


def test_unresolved_contract_is_null_with_card_warning(
    db_session: Session, audit_ctx: AuditContext, parse: Callable[[dict[str, Any]], None]
) -> None:
    w = _world(db_session)
    _second_contract(db_session, w)
    mail = _mail(db_session, w)
    parse({"lines": [_line(clock.today_sofia() + timedelta(days=1), "СА 1234 ВХ")]})
    inbound_mail.process(db_session, mail.id)
    request = db_session.scalars(select(ClientRequest)).one()
    card = db_session.scalars(select(ActionCard)).one()
    assert request.sale_contract_id is None and "избери договор" in (card.subtitle or "")
    assert card.payload["to"] == ""
    assert {n["label"]: n for n in request.agent_notes or []}["Договор"]["value"] == "не е избран"


def test_agent_failure_marks_email_failed_without_request(
    db_session: Session, audit_ctx: AuditContext, parse: Callable[[dict[str, Any]], None]
) -> None:
    w = _world(db_session)
    mail = _mail(db_session, w)
    parse({"lines": []})  # празен резултат не минава валидацията и след повторение
    assert inbound_mail.process(db_session, mail.id) is InboundStatus.FAILED
    assert db_session.scalars(select(ClientRequest)).all() == []
    assert "request_parser" in (mail.error or "")


def test_missing_plate_and_kg_quantity(
    db_session: Session, audit_ctx: AuditContext, parse: Callable[[dict[str, Any]], None]
) -> None:
    w = _world(db_session)
    mail = _mail(db_session, w)
    day = clock.today_sofia() + timedelta(days=2)
    parse({"lines": [_line(day, "", qty_t="21820"), _line(day, "СА 1234 ВХ", qty_t="48")]})
    inbound_mail.process(db_session, mail.id)
    no_plate, ok = db_session.scalars(select(ClientRequestLine).order_by(ClientRequestLine.line_no))
    assert no_plate.plate_raw == "" and "plate" in no_plate.uncertain_fields
    assert no_plate.planned_qty_t == D("21.820") and "qty" in no_plate.uncertain_fields
    assert "няма номер" in (no_plate.uncertain_note or "")
    assert ok.planned_qty_t == D("48") and ok.uncertain_fields == []


@pytest.mark.parametrize(
    ("raw", "tractor", "trailer"),  # реални двойки от прикачените xlsx (влекач / ремарке)
    [
        ("ЕН1263МА/ЕН7649ЕХ", "ЕН1263МА", "ЕН7649ЕХ"),
        ("СВ7246НХ/ СВ5874ЕА", "СВ7246НХ", "СВ5874ЕА"),
        ("А 9291 РА/ А 4722 ЕМ", "А 9291 РА", "А 4722 ЕМ"),
        ("ТХ5728АР", "ТХ5728АР", None),
    ],
)
def test_split_tractor_and_trailer(raw: str, tractor: str, trailer: str | None) -> None:
    from app.services.request_intake import split_plates

    assert split_plates(raw, None) == (tractor, trailer)
    assert split_plates(raw, "ХХ0000ЕЕ")[1] == "ХХ0000ЕЕ"  # изричното ремарке на агента печели


def test_pair_in_plate_field_saved_as_trailer(
    db_session: Session, audit_ctx: AuditContext, parse: Callable[[dict[str, Any]], None]
) -> None:
    w = _world(db_session)
    mail = _mail(db_session, w)
    parse({"lines": [_line(clock.today_sofia() + timedelta(days=2), "СВ7246НХ/ СВ5874ЕА")]})
    inbound_mail.process(db_session, mail.id)
    line = db_session.scalars(select(ClientRequestLine)).one()
    assert (line.plate_raw, line.trailer_plate) == ("СВ7246НХ", "СВ5874ЕА")
    assert line.plate_norm == "CB7246HX" and line.uncertain_fields == []


def test_lines_get_cheapest_offer_and_unmatched_lines_stay_to_choose(
    db_session: Session, audit_ctx: AuditContext, parse: Callable[[dict[str, Any]], None]
) -> None:
    from app.models.contracts import ContractPrice

    w = _world(db_session)  # покупен договор П-2026-007 в „Тервел“, цена 150 за bulk
    cheap_wh = Warehouse(counterparty_id=w.supplier.id, name="Разделна", packagings=["bulk"])
    db_session.add(cheap_wh)
    db_session.flush()
    cheap = w._contract("purchase", "П-2026-020", w.supplier, warehouse_id=cheap_wh.id)
    jan = clock.today_sofia().replace(month=1, day=1)
    db_session.add(
        ContractPrice(contract_id=cheap.id, packaging="bulk", valid_from=jan, price_per_t=D("140"))
    )
    db_session.flush()
    mail = _mail(db_session, w)
    day = clock.today_sofia() + timedelta(days=2)
    parse(
        {
            "lines": [
                _line(day, "СА 1234 ВХ", packaging_raw="насипни"),
                _line(day, "СВ 7890 ТР", packaging_raw="насипни", location_raw="Тервел"),
                _line(day, "", packaging_raw="транспорт", own_transport=True),
                _line(day, "РВ 1111 АА", packaging_raw="бегове"),  # няма склад с цена за бегове
            ]
        }
    )
    inbound_mail.process(db_session, mail.id)
    a, b, own, bagged = db_session.scalars(select(ClientRequestLine).order_by(ClientRequestLine.line_no))
    assert (a.packaging, a.purchase_contract_id, a.warehouse_id) == ("bulk", cheap.id, cheap_wh.id)
    assert (b.purchase_contract_id, b.warehouse_id) == (
        w.purchase.id,
        w.wh.id,
    )  # зачетено „Място на товарене“
    assert own.own_transport and own.plate_raw == "" and "plate" not in own.uncertain_fields
    assert (bagged.packaging, bagged.purchase_contract_id, bagged.warehouse_id) == ("bagged", None, None)
    assert [c["price_per_t"] for c in (a.offer_note or {})["candidates"]] == ["140.00", "150.00"]
    request = db_session.scalars(select(ClientRequest)).one()
    assert request.status == "draft"  # без оферта заявката не е блокирана
