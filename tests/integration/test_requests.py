"""Заявки: редакция, send/undo/finalize, SMTP грешка (agro-br9.6)."""

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.audit_context import AuditContext
from app.core.config import get_settings
from app.main import create_app
from app.models.cards import ActionCard, CardKind, CardStatus
from app.models.company import CompanySettings
from app.models.contracts import Contract
from app.models.mail import InboundEmail, OutboundEmail
from app.models.masterdata import Contact, Counterparty, Warehouse
from app.models.trips import ClientRequest, ClientRequestLine, Trip, TripEvent
from app.services import outbound as outbound_service
from app.services.cards import CardService
from app.services.users import UserService
from app.workers.tasks import mail as mail_tasks
from tests.integration.test_auth import EMAIL, PASSWORD, bearer, login
from tests.integration.test_mail_send import _env, company  # noqa: F401  (фикстури: GreenMail, фирма)
from tests.integration.test_trips import World

D = Decimal

pytestmark = [pytest.mark.usefixtures("live_mode"), pytest.mark.mail]


class Setup:
    def __init__(self, session: Session) -> None:
        self.w = World(session)
        self.session = session
        w = self.w
        contact = Contact(
            counterparty_id=w.supplier.id,
            email="supplier-orders@example.test",
            receives_orders=True,
        )
        session.add(contact)
        session.flush()
        w.purchase.order_contact_id = contact.id
        w.sale.supply_contract_id = w.purchase.id
        self.request = w.request()
        self.l1 = self.line(1, "СА 1234 ВХ", "Иван Петров", uncertain=["driver_name"])
        self.l2 = self.line(2, "СВ 7890 ТР", None)
        self.card = CardService.create(
            session,
            CardKind.CLIENT_REQUEST,
            title="Нова заявка",
            client_request_id=self.request.id,
            payload={
                "request_id": self.request.id,
                "inbound_email_id": self.request.inbound_email_id,
                "to": "supplier-orders@example.test",
                "subject": "Курсове 19.11–20.11 — Агро Трейд",
                "greeting": "Здравейте,\nИзпращаме ви курсовете:",
                "lines": [],
                "total_trucks": 2,
                "total_qty_t": "50.400",
                "has_uncertain": True,
            },
        )
        session.flush()

    def line(
        self, no: int, plate: str, driver: str | None, uncertain: list[str] | None = None
    ) -> ClientRequestLine:
        from app.domain.plates import normalize_plate

        line = ClientRequestLine(
            request_id=self.request.id,
            line_no=no,
            planned_date=date(2026, 11, 18 + no),
            plate_raw=plate,
            plate_norm=normalize_plate(plate),
            driver_name=driver,
            planned_qty_t=D("25.200"),
            uncertain_fields=uncertain or [],
            uncertain_note="писано на ръка" if uncertain else None,
        )
        self.session.add(line)
        self.session.flush()
        return line


@pytest.fixture
def s(db_session: Session, audit_ctx: AuditContext, company: CompanySettings) -> Setup:  # noqa: F811
    return Setup(db_session)


@pytest.fixture
def dispatched(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, datetime | None]]:
    calls: list[tuple[int, datetime | None]] = []
    monkeypatch.setattr(outbound_service, "dispatch_send_outbound", lambda i, eta: calls.append((i, eta)))
    return calls


@pytest.fixture
def api(db_session: Session, s: Setup) -> Iterator[TestClient]:
    UserService.create(
        db_session, email=EMAIL, full_name="Георги Гюров", short_name="Георги", password=PASSWORD
    )
    db_session.flush()
    app = create_app()

    def override_db() -> Iterator[Session]:
        db_session.commit()
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


def send(api: TestClient, auth: dict[str, str], card: ActionCard) -> dict[str, object]:
    response = api.post(f"/api/v1/cards/{card.id}/actions/send", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


def trips_of(session: Session, request: ClientRequest) -> list[Trip]:
    return list(session.scalars(select(Trip).where(Trip.request_id == request.id)))


def test_get_request_dto(api: TestClient, auth: dict[str, str], s: Setup) -> None:
    body = api.get(f"/api/v1/requests/{s.request.id}", headers=auth).json()
    assert body["status"] == "draft" and body["customer_name"]
    assert [(g["supplier_name"], g["email"]["to"], g["email"]["version"]) for g in body["groups"]] == [
        ("Агро Сует", ["supplier-orders@example.test"], 1)
    ]
    assert body["groups"][0]["line_ids"] == [s.l1.id, s.l2.id]
    assert "to" not in body and "subject" not in body
    assert body["sale_contract"] == {"id": s.w.sale.id, "number": "2026-018"}
    assert [line["plate"] for line in body["lines"]] == ["СА 1234 ВХ", "СВ 7890 ТР"]
    assert body["warnings"] == ["Агентът не е сигурен за шофьора на СА 1234 ВХ — писано на ръка"]


def test_patch_and_line_edits(api: TestClient, auth: dict[str, str], s: Setup, db_session: Session) -> None:
    url = f"/api/v1/requests/{s.request.id}"

    edit = {
        "planned_date": "2026-11-19",
        "plate": "ca 1234 bx",
        "driver_name": "Стоян",
        "planned_qty_t": "24.500",
    }
    done = api.put(f"{url}/lines/{s.l1.id}", json=edit, headers=auth).json()
    line = done["lines"][0]
    assert (line["plate"], line["driver_name"], line["planned_qty_t"]) == ("СА 1234 ВХ", "Стоян", "24.500")
    assert line["uncertain_fields"] == [] and done["warnings"] == []

    added = api.post(f"{url}/lines", json={**edit, "plate": "B 5555 KT"}, headers=auth)
    assert added.status_code == 201 and added.json()["lines"][-1]["line_no"] == 3
    removed = api.delete(f"{url}/lines/{s.l2.id}", headers=auth).json()
    assert [x["line_no"] for x in removed["lines"]] == [1, 3]
    assert db_session.get(ClientRequestLine, s.l2.id).is_removed is True  # type: ignore[union-attr]
    assert db_session.get(ActionCard, s.card.id).payload["total_trucks"] == 2  # type: ignore[union-attr]


def test_invalid_plate_is_400_with_field(api: TestClient, auth: dict[str, str], s: Setup) -> None:
    edit = {"planned_date": "2026-11-19", "plate": "123", "driver_name": None, "planned_qty_t": "25.200"}
    response = api.put(f"/api/v1/requests/{s.request.id}/lines/{s.l1.id}", json=edit, headers=auth)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert "plate" in response.json()["error"]["fields"]


def test_send_then_undo_restores_everything(
    api: TestClient,
    auth: dict[str, str],
    s: Setup,
    db_session: Session,
    dispatched: list[tuple[int, datetime | None]],
) -> None:
    result = send(api, auth, s.card)
    token = str(result["undo_token"])
    assert result["card"] is None and result["snackbar"] == "Имейлът е изпратен до Агро Сует"
    db_session.refresh(s.request)
    out = db_session.get(OutboundEmail, s.request.outbound_email_id)
    assert out is not None and (s.request.status, out.status) == ("queued", "queued")
    assert dispatched == [(out.id, out.send_after)] and out.send_after is not None
    assert trips_of(db_session, s.request) == []  # курсове при send няма

    assert api.post(f"/api/v1/undo/{token}", headers=auth).status_code == 200
    db_session.refresh(s.request)
    db_session.refresh(out)
    db_session.refresh(s.card)
    assert (s.request.status, out.status, s.card.status) == ("draft", "draft", CardStatus.OPEN.value)
    assert trips_of(db_session, s.request) == []
    assert mail_tasks.send_outbound.delay(out.id).get() is False  # планираната задача не праща

    # повторно „Изпрати“ ползва същото писмо (същият АИТ номер)
    send(api, auth, s.card)
    db_session.refresh(s.request)
    assert s.request.outbound_email_id == out.id


def test_task_creates_trips_and_sends(
    api: TestClient,
    auth: dict[str, str],
    s: Setup,
    db_session: Session,
    dispatched: list[tuple[int, datetime | None]],
) -> None:
    s.l2.is_removed = True  # изтрит ред не става курс
    db_session.flush()
    send(api, auth, s.card)
    db_session.refresh(s.request)
    out_id = s.request.outbound_email_id
    assert out_id is not None

    assert mail_tasks.send_outbound.delay(out_id).get() is True

    db_session.refresh(s.request)
    out = db_session.get(OutboundEmail, out_id)
    assert out is not None and (s.request.status, out.status) == ("sent", "sent")
    trips = trips_of(db_session, s.request)
    assert [(t.status, t.plate_display) for t in trips] == [("planned", "СА 1234 ВХ")]
    kinds = [e.kind for e in db_session.scalars(select(TripEvent).where(TripEvent.trip_id == trips[0].id))]
    assert sorted(kinds) == ["requested", "sent_to_supplier"]
    db_session.refresh(s.l1)
    assert s.l1.trip_id == trips[0].id
    assert "Общо: 1 камиона" in out.body_text and out.subject.endswith(f"[{out.reference_code}]")


def test_smtp_error_rolls_back_trips_and_reopens_card(
    api: TestClient,
    auth: dict[str, str],
    s: Setup,
    db_session: Session,
    dispatched: list[tuple[int, datetime | None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send(api, auth, s.card)
    db_session.refresh(s.request)
    out_id = s.request.outbound_email_id
    assert out_id is not None
    monkeypatch.setenv("APP_SMTP_PORT", "1")  # нищо не слуша
    get_settings.cache_clear()

    assert mail_tasks.send_outbound.delay(out_id).get() is False

    db_session.refresh(s.request)
    db_session.refresh(s.card)
    out = db_session.get(OutboundEmail, out_id)
    assert out is not None and out.status == "failed" and out.error
    assert trips_of(db_session, s.request) == []
    assert s.request.status == "queued"  # заявката остава queued; картата е отворена с грешка
    assert (s.card.status, s.card.resolution) == (CardStatus.OPEN.value, None)


def test_undo_after_send_is_410(
    api: TestClient,
    auth: dict[str, str],
    s: Setup,
    db_session: Session,
    dispatched: list[tuple[int, datetime | None]],
) -> None:
    token = str(send(api, auth, s.card)["undo_token"])
    db_session.refresh(s.request)
    assert s.request.outbound_email_id is not None
    assert mail_tasks.send_outbound.delay(s.request.outbound_email_id).get() is True
    response = api.post(f"/api/v1/undo/{token}", headers=auth)
    assert (response.status_code, response.json()["error"]["code"]) == (410, "undo_expired")
    assert api.post("/api/v1/undo/боклук", headers=auth).status_code == 410


def test_reject(api: TestClient, auth: dict[str, str], s: Setup, db_session: Session) -> None:
    response = api.post(f"/api/v1/cards/{s.card.id}/actions/reject", headers=auth)
    assert response.status_code == 200 and response.json()["card"] is None
    db_session.refresh(s.request)
    db_session.refresh(s.card)
    assert (s.request.status, s.card.status, s.card.resolution) == ("rejected", "done", "rejected")


def test_send_without_order_address_is_400(api: TestClient, auth: dict[str, str], s: Setup) -> None:
    s.w.purchase.order_contact_id = None
    s.session.flush()
    response = api.post(f"/api/v1/cards/{s.card.id}/actions/send", headers=auth)
    assert (response.status_code, response.json()["error"]["code"]) == (400, "validation_error")


def test_preview_equals_sent_body(
    api: TestClient,
    auth: dict[str, str],
    s: Setup,
    db_session: Session,
    dispatched: list[tuple[int, datetime | None]],
) -> None:
    from app.mail import smtp

    r = api.get(f"/api/v1/requests/{s.request.id}/preview", headers=auth)
    assert r.status_code == 200
    assert len(r.json()["emails"]) == 1
    pv = r.json()["emails"][0]
    assert pv["to"] == "supplier-orders@example.test" and pv["subject"].endswith("[АИТ-…]")
    assert db_session.scalar(select(OutboundEmail)) is None  # превюто не създава писмо

    send(api, auth, s.card)
    db_session.refresh(s.request)
    out = db_session.get(OutboundEmail, s.request.outbound_email_id)
    assert out is not None
    settings_row = db_session.get(CompanySettings, 1)
    assert settings_row is not None
    assert pv["body_text"] == smtp.render_body(out.body_text, settings_row.email_signature)
    assert pv["subject"].removesuffix("[АИТ-…]") == out.subject.removesuffix(f"[{out.reference_code}]")
    # след „Изпрати“ заявката не е draft → превюто е 409
    assert api.get(f"/api/v1/requests/{s.request.id}/preview", headers=auth).status_code == 409


def two_suppliers(s: Setup) -> Contract:
    """Втори доставчик с формат `per_day`; l2 отива при него, l1 остава при първия."""
    w = s.w
    other = Counterparty(name="Юропак", legal_name="Юропак АД", eik="555555555", order_format="per_day")
    s.session.add(other)
    s.session.flush()
    wh = Warehouse(counterparty_id=other.id, name="Попово")
    contact = Contact(counterparty_id=other.id, email="orders@europack.test", receives_orders=True)
    s.session.add_all([wh, contact])
    s.session.flush()
    purchase2 = Contract(
        number="П-2026-099", side="purchase", counterparty_id=other.id, product_id=w.pellets.id,
        warehouse_id=wh.id, order_contact_id=contact.id, valid_from=date(2026, 1, 1),
    )  # fmt: skip
    s.session.add(purchase2)
    s.session.flush()
    s.l1.purchase_contract_id = w.purchase.id
    s.l2.purchase_contract_id = purchase2.id
    s.l2.packaging = "bagged"
    s.session.flush()
    return purchase2


def test_one_letter_per_supplier(
    api: TestClient,
    auth: dict[str, str],
    s: Setup,
    db_session: Session,
    dispatched: list[tuple[int, datetime | None]],
) -> None:
    two_suppliers(s)
    emails = api.get(f"/api/v1/requests/{s.request.id}/preview", headers=auth).json()["emails"]
    assert [e["to"] for e in emails] == ["supplier-orders@example.test", "orders@europack.test"]
    assert "Камион" in emails[0]["body_text"] and "Опаковка" not in emails[0]["body_text"]
    assert (
        "Дата на експедиция | Вид | Камиони" in emails[1]["body_text"]
        and "опаковани" in emails[1]["body_text"]
    )

    token = str(send(api, auth, s.card)["undo_token"])
    outs = list(db_session.scalars(select(OutboundEmail).order_by(OutboundEmail.id)))
    assert len(outs) == 2 and len(dispatched) == 2 and {o.status for o in outs} == {"queued"}
    assert [o.to_addresses for o in outs] == [["supplier-orders@example.test"], ["orders@europack.test"]]

    # „Отмени“ връща и двете писма
    assert api.post(f"/api/v1/undo/{token}", headers=auth).status_code == 200
    db_session.expire_all()
    assert {o.status for o in db_session.scalars(select(OutboundEmail))} == {"draft"}
    db_session.refresh(s.request)
    assert s.request.status == "draft"

    # повторно „Изпрати“ преизползва същите писма (без нови АИТ номера)
    send(api, auth, s.card)
    assert [o.id for o in db_session.scalars(select(OutboundEmail).order_by(OutboundEmail.id))] == [
        o.id for o in outs
    ]

    # заявката е `sent` едва след второто писмо
    assert mail_tasks.send_outbound.delay(outs[0].id).get() is True
    db_session.refresh(s.request)
    assert s.request.status == "queued"
    assert mail_tasks.send_outbound.delay(outs[1].id).get() is True
    db_session.refresh(s.request)
    assert s.request.status == "sent"
    trips = sorted(trips_of(db_session, s.request), key=lambda t: t.planned_date)
    assert [t.purchase_contract_id for t in trips] == [s.l1.purchase_contract_id, s.l2.purchase_contract_id]
    assert trips[0].supplier_id != trips[1].supplier_id


def test_patch_email_uses_email_version_and_feeds_preview_and_send(
    api: TestClient,
    auth: dict[str, str],
    s: Setup,
    db_session: Session,
    dispatched: list[tuple[int, datetime | None]],
) -> None:
    from app.mail import smtp

    p2 = two_suppliers(s)
    url = f"/api/v1/requests/{s.request.id}"
    dto = api.get(url, headers=auth).json()
    sup1, sup2 = (g["supplier_id"] for g in dto["groups"])
    assert sup2 == p2.counterparty_id

    edit = {
        "version": 1,
        "subject": "Нова тема",
        "to": ["A@Pelet.test", "b@pelet.test"],
        "body_intro": "Здрасти",
    }
    patched = api.patch(f"{url}/emails/{sup1}", json=edit, headers=auth)
    assert patched.status_code == 200
    first, second = patched.json()["groups"]
    assert first["email"] == {
        "to": ["a@pelet.test", "b@pelet.test"],
        "subject": "Нова тема",
        "body_intro": "Здрасти",
        "version": 2,
    }
    assert (
        second["email"]["version"] == 1 and patched.json()["version"] == dto["version"]
    )  # другото писмо и заявката — недокоснати
    stale = api.patch(f"{url}/emails/{sup1}", json={"version": 1, "subject": "x"}, headers=auth)
    assert (stale.status_code, stale.json()["error"]["code"]) == (409, "version_conflict")
    # другият доставчик не е засегнат от версията на първия
    assert (
        api.patch(f"{url}/emails/{sup2}", json={"version": 1, "subject": "Втора"}, headers=auth).status_code
        == 200
    )
    assert api.patch(f"{url}/emails/999999", json={"version": 1}, headers=auth).status_code == 404
    bad = api.patch(f"{url}/emails/{sup1}", json={"version": 2, "to": ["без-маймунка"]}, headers=auth)
    assert (bad.status_code, bad.json()["error"]["code"]) == (400, "validation_error")
    # явен null връща подразбиращото се
    reset = api.patch(f"{url}/emails/{sup1}", json={"version": 2, "subject": None}, headers=auth).json()
    assert reset["groups"][0]["email"]["subject"].startswith("Курсове") and reset["groups"][0]["email"][
        "to"
    ] == ["a@pelet.test", "b@pelet.test"]
    api.patch(f"{url}/emails/{sup1}", json={"version": 3, "subject": "Нова тема"}, headers=auth)

    emails = api.get(f"{url}/preview", headers=auth).json()["emails"]
    assert emails[0]["to"] == "a@pelet.test, b@pelet.test" and emails[0]["subject"] == "Нова тема [АИТ-…]"
    assert emails[0]["body_text"].startswith("Здрасти") and emails[1]["subject"] == "Втора [АИТ-…]"

    send(api, auth, s.card)
    outs = list(db_session.scalars(select(OutboundEmail).order_by(OutboundEmail.id)))
    settings_row = db_session.get(CompanySettings, 1)
    assert settings_row is not None
    assert outs[0].to_addresses == ["a@pelet.test", "b@pelet.test"]
    for pv, out in zip(emails, outs, strict=True):
        assert pv["body_text"] == smtp.render_body(out.body_text, settings_row.email_signature)
        assert pv["subject"].removesuffix("[АИТ-…]") == out.subject.removesuffix(f"[{out.reference_code}]")


def no_warehouse(s: Setup) -> None:
    """l2 остава без склад/доставчик („избери“), l1 — при първия доставчик."""
    s.l1.purchase_contract_id = s.w.purchase.id
    s.l2.purchase_contract_id = None
    s.l2.warehouse_id = None
    s.w.sale.supply_contract_id = None
    s.session.flush()


def test_no_warehouse_group_and_partial_send(
    api: TestClient,
    auth: dict[str, str],
    s: Setup,
    db_session: Session,
    dispatched: list[tuple[int, datetime | None]],
) -> None:
    no_warehouse(s)
    url = f"/api/v1/requests/{s.request.id}"
    dto = api.get(url, headers=auth).json()
    assert [(g["supplier_id"], g["line_ids"], g["email"] is None) for g in dto["groups"]] == [
        (s.w.supplier.id, [s.l1.id], False),
        (None, [s.l2.id], True),
    ]
    assert [e["to"] for e in api.get(f"{url}/preview", headers=auth).json()["emails"]] == [
        "supplier-orders@example.test"
    ]

    token = str(send(api, auth, s.card)["undo_token"])
    db_session.refresh(s.request)
    db_session.refresh(s.card)
    assert (s.request.status, s.card.status) == ("draft", "open")  # остава ред без склад
    out = db_session.scalars(select(OutboundEmail)).one()
    assert out.status == "queued" and len(dispatched) == 1
    assert db_session.get(ClientRequestLine, s.l1.id).outbound_email_id == out.id  # type: ignore[union-attr]
    assert db_session.get(ClientRequestLine, s.l2.id).outbound_email_id is None  # type: ignore[union-attr]

    # изпратеният ред не се редактира; празният ред — да
    edit = {"planned_date": "2026-11-19", "plate": "СА 1234 ВХ", "planned_qty_t": "1.000"}
    assert api.put(f"{url}/lines/{s.l1.id}", json=edit, headers=auth).status_code == 409
    assert (
        api.put(f"{url}/lines/{s.l2.id}", json={**edit, "plate": "СВ 7890 ТР"}, headers=auth).status_code
        == 200
    )
    # повторно „Изпрати“ без нови редове с доставчик → 400, не дублира писмо
    again = api.post(f"/api/v1/cards/{s.card.id}/actions/send", headers=auth)
    assert (again.status_code, again.json()["error"]["code"]) == (400, "validation_error")

    # „Отмени“ връща само изпратения ред
    assert api.post(f"/api/v1/undo/{token}", headers=auth).status_code == 200
    db_session.expire_all()
    assert db_session.get(OutboundEmail, out.id).status == "draft"  # type: ignore[union-attr]
    assert db_session.get(ClientRequest, s.request.id).status == "draft"  # type: ignore[union-attr]
    assert [e["supplier_name"] for e in api.get(f"{url}/preview", headers=auth).json()["emails"]] == [
        "Агро Сует"
    ]


def test_partial_send_then_rest_completes_request(
    api: TestClient,
    auth: dict[str, str],
    s: Setup,
    db_session: Session,
    dispatched: list[tuple[int, datetime | None]],
) -> None:
    no_warehouse(s)
    p2 = two_suppliers(s)
    s.l2.purchase_contract_id = None  # two_suppliers го е насочил към втория доставчик
    s.l2.warehouse_id = None
    s.session.flush()
    send(api, auth, s.card)
    first = db_session.scalars(select(OutboundEmail)).one()
    assert mail_tasks.send_outbound.delay(first.id).get() is True
    db_session.refresh(s.request)
    assert s.request.status == "draft"  # l2 още няма курс

    s.l2.purchase_contract_id = p2.id
    s.session.flush()
    token2 = str(send(api, auth, s.card)["undo_token"])
    db_session.refresh(s.request)
    db_session.refresh(s.card)
    assert (s.request.status, s.card.status) == ("queued", "done")
    outs = list(db_session.scalars(select(OutboundEmail).order_by(OutboundEmail.id)))
    assert len(outs) == 2 and outs[0].status == "sent" and outs[1].status == "queued"
    # „Отмени“ на второто изпращане не пипа първото
    assert api.post(f"/api/v1/undo/{token2}", headers=auth).status_code == 200
    db_session.expire_all()
    assert [o.status for o in db_session.scalars(select(OutboundEmail).order_by(OutboundEmail.id))] == [
        "sent",
        "draft",
    ]
    send(api, auth, s.card)
    assert mail_tasks.send_outbound.delay(outs[1].id).get() is True
    db_session.refresh(s.request)
    assert s.request.status == "sent"


def test_all_lines_without_supplier_is_400(api: TestClient, auth: dict[str, str], s: Setup) -> None:
    s.w.sale.supply_contract_id = None  # няма насрещен договор → всички редове са „избери“
    s.session.flush()
    dto = api.get(f"/api/v1/requests/{s.request.id}", headers=auth).json()
    assert all(line["needs_choice"] for line in dto["lines"])  # заявката се чете и редактира
    assert [(g["supplier_id"], g["email"]) for g in dto["groups"]] == [(None, None)]
    for response in (
        api.post(f"/api/v1/cards/{s.card.id}/actions/send", headers=auth),
        api.get(f"/api/v1/requests/{s.request.id}/preview", headers=auth),
    ):
        assert (response.status_code, response.json()["error"]["code"]) == (400, "validation_error")


def test_choosing_warehouse_picks_cheapest_contract_there(
    api: TestClient, auth: dict[str, str], s: Setup, db_session: Session
) -> None:
    s.w.sale.supply_contract_id = None
    s.l1.packaging = "bulk"
    db_session.flush()  # World слага цена 150 (bulk) на покупния договор
    url = f"/api/v1/requests/{s.request.id}/lines/{s.l1.id}"
    body = {
        "planned_date": "2026-11-19", "plate": "СА 1234 ВХ", "driver_name": "Иван Петров",
        "planned_qty_t": "25.200", "warehouse_id": s.w.wh.id,
    }  # fmt: skip
    dto = api.put(url, json=body, headers=auth).json()
    line = next(x for x in dto["lines"] if x["id"] == s.l1.id)
    assert (line["warehouse"]["id"], line["purchase_contract_id"], line["needs_choice"]) == (
        s.w.wh.id,
        s.w.purchase.id,
        False,
    )
    assert line["offer_note"]["manual"] is True and line["offer_note"]["chosen"]["price_per_t"] == "150.00"


def test_offers_endpoint_and_clearing_warehouse(
    api: TestClient, auth: dict[str, str], s: Setup, db_session: Session
) -> None:
    s.w.sale.supply_contract_id = None
    s.l1.packaging = "bulk"
    db_session.flush()
    offers = api.get(f"/api/v1/requests/{s.request.id}/lines/{s.l1.id}/offers", headers=auth).json()
    assert offers == [
        {
            "warehouse": {"id": s.w.wh.id, "name": "Тервел", "supplier_name": s.w.supplier.name},
            "contract_id": s.w.purchase.id,
            "price_per_t": "150.00",
            "is_cheapest": True,
        }
    ]
    url = f"/api/v1/requests/{s.request.id}/lines/{s.l1.id}"
    body = {"planned_date": "2026-11-19", "plate": "СА 1234 ВХ", "planned_qty_t": "25.200"}
    chosen = api.put(url, json={**body, "warehouse_id": s.w.wh.id}, headers=auth).json()["lines"][0]
    assert chosen["purchase_contract_id"] == s.w.purchase.id
    cleared = api.put(url, json={**body, "warehouse_id": None}, headers=auth).json()["lines"][0]
    assert (cleared["warehouse"], cleared["purchase_contract_id"], cleared["needs_choice"]) == (
        None,
        None,
        True,
    )
    # без warehouse_id в тялото изборът не се пипа
    api.put(url, json={**body, "warehouse_id": s.w.wh.id}, headers=auth)
    kept = api.put(url, json=body, headers=auth).json()["lines"][0]
    assert kept["purchase_contract_id"] == s.w.purchase.id


def confirmation_world(s: Setup, tmp_path: Path, adapter: str | None) -> None:
    from openpyxl import Workbook

    from app.models.mail import EmailAttachment

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws["G1"], ws["I2"] = "Дата на изпълнение", "Място на товарене"
    ws.cell(3, 7, s.l1.planned_date)
    ws.cell(4, 7, s.l2.planned_date)
    path = tmp_path / "raboten.xlsx"
    wb.save(path)
    inbound = s.session.get(InboundEmail, s.request.inbound_email_id)
    assert inbound is not None
    inbound.subject = "седмица 47"
    att = EmailAttachment(
        inbound_email_id=inbound.id, filename="Raboten fail.xlsx", content_type="x", size_bytes=1,
        sha256="0" * 64, storage_path=str(path),
    )  # fmt: skip
    s.session.add(att)
    s.session.flush()
    s.request.source_attachment_id = att.id
    s.request.status = "sent"
    s.l1.source_row, s.l2.source_row = 3, 4
    s.l1.warehouse_id = s.w.wh.id  # l2 остава „избери“ → предупреждение
    s.w.client.client_file_adapter = adapter
    s.session.flush()


def test_client_confirmation_with_ameta_adapter(
    api: TestClient,
    auth: dict[str, str],
    s: Setup,
    db_session: Session,
    tmp_path: Path,
    dispatched: list[tuple[int, datetime | None]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    from openpyxl import load_workbook

    from app.models.mail import EmailAttachment

    monkeypatch.setenv("APP_STORAGE_DIR", str(tmp_path / "storage"))
    get_settings.cache_clear()
    confirmation_world(s, tmp_path, "ameta")
    url = f"/api/v1/requests/{s.request.id}/client-confirmation"
    pv = api.get(url, headers=auth).json()
    assert pv["subject"] == "Re: седмица 47 [АИТ-…]" and pv["to"] == "maria@agrotrade.bg"
    assert pv["attachment"] == {"filename": "Raboten fail.xlsx", "filled_rows": 1}
    assert "Потвърждавам графика" in pv["body_text"] and len(pv["warnings"]) == 1

    assert api.post(f"{url}/send", headers=auth).status_code == 202
    out = db_session.scalars(
        select(OutboundEmail).where(OutboundEmail.purpose == "client_confirmation")
    ).one()
    assert out.status == "queued" and out.to_addresses == ["maria@agrotrade.bg"] and len(dispatched) == 1
    att = db_session.scalars(select(EmailAttachment).where(EmailAttachment.outbound_email_id == out.id)).one()
    assert load_workbook(att.storage_path).worksheets[0]["I3"].value == "Тервел"
    assert api.post(f"{url}/send", headers=auth).status_code == 409  # само веднъж


def test_client_without_adapter_gets_only_the_letter(
    api: TestClient, auth: dict[str, str], s: Setup, tmp_path: Path
) -> None:
    confirmation_world(s, tmp_path, None)
    pv = api.get(f"/api/v1/requests/{s.request.id}/client-confirmation", headers=auth).json()
    assert pv["attachment"] is None and pv["warnings"] == []


def test_confirmation_only_after_suppliers_sent(api: TestClient, auth: dict[str, str], s: Setup) -> None:
    response = api.get(f"/api/v1/requests/{s.request.id}/client-confirmation", headers=auth)
    assert (response.status_code, response.json()["error"]["code"]) == (409, "invalid_transition")
