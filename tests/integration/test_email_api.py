"""GET /emails/{id} (agro-br9.9, agro-sw1): форма по api.md §3, call_phone, rows, agent_notes."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.audit_context import AuditContext
from app.main import create_app
from app.models.mail import EmailAttachment, InboundEmail, MatchMethod
from app.models.masterdata import Contact
from app.models.trips import ClientRequestLine
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


@pytest.fixture
def auth(api: TestClient) -> dict[str, str]:
    return bearer(login(api).json()["access_token"])


def get(api: TestClient, auth: dict[str, str], email_id: int):
    return api.get(f"/api/v1/emails/{email_id}", headers=auth)


def test_shape_phone_from_contact_and_request_notes(
    api: TestClient, auth: dict[str, str], w: World, db_session: Session, tmp_path: Path
) -> None:
    request = w.request()
    mail = db_session.get(InboundEmail, request.inbound_email_id)
    assert mail is not None
    contact = Contact(counterparty_id=w.client.id, email="maria@agrotrade.bg", phone="+359888123456")
    db_session.add(contact)
    db_session.flush()
    w.client.phone = "+35929999999"
    mail.counterparty_id, mail.contact_id = w.client.id, contact.id
    mail.match_method = MatchMethod.CONTACT.value
    mail.to_addresses = ["orders@ours.test"]
    mail.from_name = "Мария"
    request.agent_notes = [{"label": "Вид", "value": "Заявка за курсове", "why": "по текста"}]
    db_session.add(
        ClientRequestLine(
            request_id=request.id,
            line_no=1,
            planned_date=request.created_at.date(),
            plate_raw="СВ 7890 ТР",
            plate_norm="СВ7890ТР",
            planned_qty_t=25,
            uncertain_fields=["driver"],
            uncertain_note="почеркът е нечетлив",
        )
    )
    xlsx = tmp_path / "r.xlsx"
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(["Дата", "Номер"])
    ws.append(["19.11", "СВ 7890 ТР"])
    ws.append(["20.11", "СА 1234 ВХ"])
    wb.save(xlsx)
    db_session.add_all(
        [
            EmailAttachment(
                inbound_email_id=mail.id,
                filename="r.xlsx",
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                size_bytes=xlsx.stat().st_size,
                sha256="a" * 64,
                storage_path=str(xlsx),
            ),
            EmailAttachment(
                inbound_email_id=mail.id,
                filename="n.pdf",
                content_type="application/pdf",
                size_bytes=5,
                sha256="b" * 64,
                storage_path="/nope",
            ),
        ]
    )
    db_session.flush()

    body = get(api, auth, mail.id).json()
    assert body["from_name"] == "Мария"
    assert body["to"] == ["orders@ours.test"]
    assert body["verified_sender"] is True
    assert body["counterparty"] == {"id": w.client.id, "name": w.client.name}
    assert body["match_label"] == f"{w.client.name} · контакт"
    assert body["call_phone"] == "+359888123456"  # контактът бие телефона на контрагента
    assert [(a["filename"], a["rows"]) for a in body["attachments"]] == [("r.xlsx", 2), ("n.pdf", None)]
    assert body["agent_notes"] == [{"label": "Вид", "value": "Заявка за курсове", "why": "по текста"}]
    assert body["uncertain"] == [{"label": "Шофьор на СВ 7890 ТР", "why": "почеркът е нечетлив"}]
    assert body["received_at"].endswith("Z")


def test_call_phone_falls_back_to_counterparty_then_null(
    api: TestClient, auth: dict[str, str], w: World, db_session: Session
) -> None:
    mail = db_session.get(InboundEmail, w.request().inbound_email_id)
    assert mail is not None
    mail.counterparty_id = w.client.id
    w.client.phone = "+35929999999"
    db_session.flush()
    assert get(api, auth, mail.id).json()["call_phone"] == "+35929999999"

    # контактът без телефон не пречи; контрагентът без телефон → null
    contact = Contact(counterparty_id=w.client.id, email="m@agrotrade.bg")
    db_session.add(contact)
    db_session.flush()
    mail.contact_id = contact.id
    w.client.phone = None
    db_session.flush()
    assert get(api, auth, mail.id).json()["call_phone"] is None


def test_unmatched_sender_and_not_found(
    api: TestClient, auth: dict[str, str], w: World, db_session: Session
) -> None:
    mail = db_session.get(InboundEmail, w.request().inbound_email_id)
    assert mail is not None
    body = get(api, auth, mail.id).json()
    assert body["counterparty"] is None
    assert body["match_label"] is None
    assert body["call_phone"] is None
    assert body["attachments"] == []
    assert body["agent_notes"] == []
    assert get(api, auth, 999_999).status_code == 404


def test_requires_auth(api: TestClient) -> None:
    assert api.get("/api/v1/emails/1").status_code == 401


def test_attachment_download(
    api: TestClient, auth: dict[str, str], w: World, db_session: Session, tmp_path: Path
) -> None:
    mail = db_session.get(InboundEmail, w.request().inbound_email_id)
    assert mail is not None
    path = tmp_path / "x.pdf"
    path.write_bytes(b"%PDF-1.4 test")
    att = EmailAttachment(
        inbound_email_id=mail.id,
        filename="Фактура.pdf",
        content_type="application/pdf",
        size_bytes=13,
        sha256="c" * 64,
        storage_path=str(path),
    )
    gone = EmailAttachment(
        inbound_email_id=mail.id,
        filename="g.pdf",
        content_type="application/pdf",
        size_bytes=1,
        sha256="d" * 64,
        storage_path=str(tmp_path / "missing.pdf"),
    )
    db_session.add_all([att, gone])
    db_session.flush()

    response = api.get(f"/api/v1/attachments/{att.id}", headers=auth)
    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 test"
    assert response.headers["content-type"] == "application/pdf"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "filename*=utf-8''%D0%A4%D0%B0%D0%BA%D1%82%D1%83%D1%80%D0%B0.pdf" in disposition
    assert api.get(f"/api/v1/attachments/{gone.id}", headers=auth).status_code == 404
    assert api.get("/api/v1/attachments/999999", headers=auth).status_code == 404
    assert api.get(f"/api/v1/attachments/{att.id}").status_code == 401
