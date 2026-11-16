"""Заявки: редакция, send/undo/finalize, SMTP грешка (agro-br9.6)."""

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal

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
from app.models.mail import OutboundEmail
from app.models.masterdata import Contact
from app.models.trips import ClientRequest, ClientRequestLine, Trip, TripEvent
from app.services import outbound as outbound_service
from app.services.cards import CardService
from app.services.users import UserService
from app.workers.tasks import mail as mail_tasks
from tests.integration.test_auth import EMAIL, PASSWORD, bearer, login
from tests.integration.test_mail_send import _env, company  # noqa: F401  (фикстури: GreenMail, фирма)
from tests.integration.test_trips import World

D = Decimal


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
    assert (body["to"], body["supplier_name"], body["status"]) == (
        "supplier-orders@example.test",
        "Агро Сует",
        "draft",
    )
    assert body["sale_contract"] == {"id": s.w.sale.id, "number": "2026-018"}
    assert [line["plate"] for line in body["lines"]] == ["СА 1234 ВХ", "СВ 7890 ТР"]
    assert body["warnings"] == ["Агентът не е сигурен за шофьора на СА 1234 ВХ — писано на ръка"]


def test_patch_and_line_edits(api: TestClient, auth: dict[str, str], s: Setup, db_session: Session) -> None:
    url = f"/api/v1/requests/{s.request.id}"
    version = api.get(url, headers=auth).json()["version"]
    patched = api.patch(url, json={"version": version, "subject": "Нова тема"}, headers=auth).json()
    assert patched["subject"] == "Нова тема" and patched["version"] == version + 1
    stale = api.patch(url, json={"version": version, "subject": "x"}, headers=auth)
    assert (stale.status_code, stale.json()["error"]["code"]) == (409, "version_conflict")

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
