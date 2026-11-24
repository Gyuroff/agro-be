"""Издаване на фактура в режим erp (agro-66f.2): номер от Мега, дати, PDF, писмо, API."""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from freezegun import freeze_time
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.audit_context import AuditContext
from app.core.config import get_settings
from app.core.errors import DomainError
from app.invoicing import numbers
from app.invoicing.pdf import render_invoice_html, render_invoice_pdf
from app.main import create_app
from app.models.cards import ActionCard, CardKind, CardResolution, CardStatus
from app.models.company import CompanySettings
from app.models.invoicing import Invoice, InvoiceStatus
from app.models.mail import EmailAttachment, OutboundEmail, OutboundPurpose, OutboundStatus
from app.models.trips import TripEvent
from app.services import invoice_service
from app.services import outbound as outbound_service
from app.services.invoice_service import InvoiceService
from app.services.invoicing import run_daily_billing
from app.services.users import UserService
from tests.integration.test_auth import EMAIL, PASSWORD, bearer, login
from tests.integration.test_invoicing import World, lines_of
from tests.integration.test_mail_send import _env, company  # noqa: F401  (фикстури)

FAKE_PDF = b"%PDF-1.4 fake"


class FakeMega:
    """Мега: следващият свободен номер. Не го резервира — като истинския."""

    def __init__(self, next_number: int = 1525) -> None:
        self.next = next_number

    def next_number(self) -> int:
        return self.next


@pytest.fixture
def mega() -> Iterator[FakeMega]:
    fake = FakeMega()
    numbers.set_number_provider(fake)
    yield fake
    numbers.set_number_provider(numbers.UnconfiguredNumberProvider())


@pytest.fixture
def delivered(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _env: None) -> list[int]:  # noqa: F811
    """Без Celery и без истински PDF (WeasyPrint иска pango): записваме извикванията."""
    monkeypatch.setenv("APP_STORAGE_DIR", str(tmp_path))
    get_settings.cache_clear()
    calls: list[int] = []
    monkeypatch.setattr(invoice_service, "dispatch_deliver", calls.append)
    monkeypatch.setattr(outbound_service, "dispatch_send_outbound", lambda i, eta: None)
    monkeypatch.setattr(invoice_service, "render_invoice_pdf", lambda inv, lines: FAKE_PDF)
    return calls


class Setup:
    def __init__(self, session: Session, mode: str = "erp") -> None:
        self.session = session
        self.w = World(session, mode=mode)
        self.trips = self.w.figma_week()
        with freeze_time("2026-11-16 05:30:00"):
            (self.invoice_id,) = run_daily_billing(session, date(2026, 11, 16))
        self.invoice = session.get_one(Invoice, self.invoice_id)


@pytest.fixture
def s(db_session: Session, audit_ctx: AuditContext, company: CompanySettings) -> Setup:  # noqa: F811
    return Setup(db_session)


def test_issue_takes_next_number_from_mega(
    s: Setup, db_session: Session, mega: FakeMega, delivered: list[int]
) -> None:
    with freeze_time("2026-11-16 07:00:00"):
        inv = InvoiceService.issue(db_session, s.invoice_id, s.invoice.version)
    assert (inv.number, inv.status) == (1525, InvoiceStatus.ISSUED.value)
    assert inv.number_sequence_id is None  # номерът е от Мега, не от number_sequences
    assert inv.issue_date == inv.tax_event_date == date(2026, 11, 16)
    assert inv.due_date == date(2026, 11, 23)  # 7 дни срок от деня на издаване
    assert inv.issued_at is not None and inv.issued_by_id is not None
    assert all(t.invoice_id == inv.id for t in s.trips)
    events = db_session.scalars(select(TripEvent).where(TripEvent.kind == "invoiced")).all()
    assert len(events) == len(s.trips) and "0000001525" in events[0].text
    db_session.commit()
    assert delivered == [inv.id]  # доставката се пуска едва след commit


def test_issue_is_audited_and_forbidden_issue_is_not(
    s: Setup, db_session: Session, mega: FakeMega, delivered: list[int]
) -> None:
    from app.models.audit import AuditLog

    def rows() -> list[AuditLog]:
        db_session.flush()
        return list(
            db_session.scalars(
                select(AuditLog).where(AuditLog.entity_type == "invoices", AuditLog.entity_id == s.invoice_id)
            )
        )

    InvoiceService.issue(db_session, s.invoice_id, s.invoice.version)
    issued = [r for r in rows() if r.changes.get("status") == ["draft", "issued"]]
    assert len(issued) == 1 and issued[0].actor_id is not None
    before = len(rows())
    with pytest.raises(DomainError) as err:
        InvoiceService.issue(db_session, s.invoice_id, s.invoice.version)
    assert err.value.http_status == 409
    assert len(rows()) == before


def test_rollback_does_not_schedule_delivery(
    s: Setup, db_session: Session, mega: FakeMega, delivered: list[int]
) -> None:
    InvoiceService.issue(db_session, s.invoice_id, s.invoice.version)
    db_session.rollback()
    assert delivered == []


def test_issue_in_shadow_is_409(
    db_session: Session,
    audit_ctx: AuditContext,
    company: CompanySettings,  # noqa: F811
    mega: FakeMega,
) -> None:
    sh = Setup(db_session, mode="shadow")
    with pytest.raises(Exception) as err:
        InvoiceService.issue(db_session, sh.invoice_id, sh.invoice.version)
    assert (err.value.code, err.value.http_status) == ("invalid_transition", 409)  # type: ignore[attr-defined]


def test_stale_version_is_409(s: Setup, db_session: Session, mega: FakeMega, delivered: list[int]) -> None:
    with pytest.raises(Exception) as err:
        InvoiceService.issue(db_session, s.invoice_id, s.invoice.version + 1)
    assert err.value.code == "version_conflict"  # type: ignore[attr-defined]
    assert s.invoice.number is None


def test_number_already_issued_is_refused(
    s: Setup, db_session: Session, mega: FakeMega, delivered: list[int]
) -> None:
    InvoiceService.issue(db_session, s.invoice_id, s.invoice.version)
    # Мега още връща 1525 (не е видяла нашата фактура) → отказ, номерът не се издава два пъти.
    clone = Invoice(
        counterparty_id=s.w.client.id,
        mode="erp",
        billing_date=date(2026, 11, 23),
        period_from=date(2026, 11, 16),
        period_to=date(2026, 11, 22),
        subtotal=Decimal("1.00"),
        vat_amount=Decimal("0.20"),
        total=Decimal("1.20"),
    )
    db_session.add(clone)
    db_session.flush()
    with pytest.raises(Exception) as err:
        InvoiceService.issue(db_session, clone.id, clone.version)
    assert err.value.code == "number_taken"  # type: ignore[attr-defined]


def test_unconfigured_mega_is_503(s: Setup, db_session: Session) -> None:
    numbers.set_number_provider(numbers.UnconfiguredNumberProvider())
    with pytest.raises(Exception) as err:
        InvoiceService.issue(db_session, s.invoice_id, s.invoice.version)
    assert (err.value.code, err.value.http_status) == ("mega_number_unavailable", 503)  # type: ignore[attr-defined]
    assert s.invoice.status == InvoiceStatus.DRAFT.value


def test_failure_after_commit_keeps_number_and_deliver_catches_up(
    s: Setup, db_session: Session, mega: FakeMega, delivered: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    InvoiceService.issue(db_session, s.invoice_id, s.invoice.version)
    db_session.commit()

    def boom(inv: Invoice, lines: object) -> bytes:
        raise RuntimeError("pdf")

    monkeypatch.setattr(invoice_service, "render_invoice_pdf", boom)
    with pytest.raises(RuntimeError):
        InvoiceService.deliver(db_session, s.invoice_id)
    db_session.rollback()
    inv = db_session.get_one(Invoice, s.invoice_id)
    assert (inv.number, inv.status, inv.pdf_path, inv.outbound_email_id) == (1525, "issued", None, None)

    mega.next = 1526  # Мега вече е продължила; нашият номер не се мени и не се взема нов
    monkeypatch.setattr(invoice_service, "render_invoice_pdf", lambda i, ls: FAKE_PDF)
    assert InvoiceService.deliver(db_session, s.invoice_id) is True
    assert InvoiceService.deliver(db_session, s.invoice_id) is True  # идемпотентна: едно писмо
    inv = db_session.get_one(Invoice, s.invoice_id)
    assert inv.number == 1525 and inv.pdf_path is not None
    mails = db_session.scalars(select(OutboundEmail).where(OutboundEmail.purpose == "invoice")).all()
    assert len(mails) == 1


def test_deliver_creates_queued_mail_with_pdf(
    s: Setup, db_session: Session, mega: FakeMega, delivered: list[int]
) -> None:
    InvoiceService.issue(db_session, s.invoice_id, s.invoice.version)
    InvoiceService.deliver(db_session, s.invoice_id)
    inv = db_session.get_one(Invoice, s.invoice_id)
    mail = db_session.get_one(OutboundEmail, inv.outbound_email_id)
    assert mail.purpose == OutboundPurpose.INVOICE.value and mail.status == OutboundStatus.QUEUED.value
    assert mail.to_addresses == ["invoices@agrotrade.bg"]
    assert "0000001525" in mail.subject
    (att,) = db_session.scalars(select(EmailAttachment).where(EmailAttachment.outbound_email_id == mail.id))
    assert att.content_type == "application/pdf" and Path(att.storage_path).read_bytes() == FAKE_PDF
    assert att.filename == "Фактура-0000001525.pdf"


def test_deliver_ignores_draft(s: Setup, db_session: Session, delivered: list[int]) -> None:
    assert InvoiceService.deliver(db_session, s.invoice_id) is False


def test_html_has_requisites_and_draft_watermark(s: Setup, db_session: Session) -> None:
    html = render_invoice_html(s.invoice, lines_of(db_session, s.invoice))
    assert "ЧЕРНОВА" in html and "Агро Трейд ООД" in html and "111111111" in html
    assert "39421.44" in html and "6570.24" in html and "32851.20" in html
    s.invoice.status = "issued"
    s.invoice.number = 1525
    html = render_invoice_html(s.invoice, lines_of(db_session, s.invoice))
    assert "ЧЕРНОВА" not in html and "0000001525" in html


def test_real_pdf_renders(s: Setup, db_session: Session) -> None:
    try:
        data = render_invoice_pdf(s.invoice, lines_of(db_session, s.invoice))
    except OSError:
        pytest.skip("WeasyPrint: липсват системни библиотеки (pango)")
    assert data.startswith(b"%PDF")


# --- API ---------------------------------------------------------------------------------------------


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


def test_api_get_draft_then_issue_then_second_user_gets_409(
    api: TestClient, auth: dict[str, str], s: Setup, mega: FakeMega, delivered: list[int]
) -> None:
    got = api.get(f"/api/v1/invoices/{s.invoice_id}", headers=auth)
    assert got.status_code == 200, got.text
    body = got.json()
    assert (body["status"], body["number_display"], body["next_number_display"]) == (
        "draft",
        None,
        "0000001525",
    )
    assert body["total"] == "39421.44" and body["trips"]["count"] == 11
    assert [x["key"] for x in body["steps"]] == ["issued", "emailed", "mega_export"]

    first = api.post(
        f"/api/v1/invoices/{s.invoice_id}/issue", json={"version": body["version"]}, headers=auth
    )
    assert first.status_code == 200, first.text
    second = api.post(
        f"/api/v1/invoices/{s.invoice_id}/issue", json={"version": body["version"]}, headers=auth
    )
    assert second.status_code == 409 and second.json()["error"]["code"] == "invalid_transition"
    issued = api.get(f"/api/v1/invoices/{s.invoice_id}", headers=auth).json()
    assert issued["number_display"] == "0000001525" and issued["next_number_display"] is None
    assert issued["steps"][0]["done"] is True and issued["steps"][0]["by"] == "Георги"
    assert delivered == [s.invoice_id]


def test_api_pdf_and_resend(
    api: TestClient, auth: dict[str, str], s: Setup, mega: FakeMega, delivered: list[int]
) -> None:
    draft = api.get(f"/api/v1/invoices/{s.invoice_id}/pdf", headers=auth)
    assert draft.status_code == 200 and draft.headers["content-type"] == "application/pdf"
    assert api.post(f"/api/v1/invoices/{s.invoice_id}/resend", headers=auth).status_code == 409

    version = api.get(f"/api/v1/invoices/{s.invoice_id}", headers=auth).json()["version"]
    api.post(f"/api/v1/invoices/{s.invoice_id}/issue", json={"version": version}, headers=auth)
    pdf = api.get(f"/api/v1/invoices/{s.invoice_id}/pdf", headers=auth)
    assert pdf.content == FAKE_PDF
    assert api.post(f"/api/v1/invoices/{s.invoice_id}/resend", headers=auth).status_code == 200
    assert api.get("/api/v1/invoices/999999", headers=auth).status_code == 404


def test_card_action_issue_resolves_card(
    api: TestClient, auth: dict[str, str], s: Setup, mega: FakeMega, delivered: list[int], db_session: Session
) -> None:
    db_session.get_one(CompanySettings, 1).action_mode = "live"
    card = db_session.scalars(select(ActionCard).where(ActionCard.kind == CardKind.INVOICE_DUE.value)).one()
    response = api.post(
        f"/api/v1/cards/{card.id}/actions/issue",
        json={"invoice_version": s.invoice.version},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    assert "0000001525" in response.json()["snackbar"]
    db_session.refresh(card)
    assert (card.status, card.resolution) == (CardStatus.DONE.value, CardResolution.ISSUED.value)
    assert db_session.get_one(Invoice, s.invoice_id).number == 1525


def test_card_action_issue_in_advisory_mode_issues_nothing(
    api: TestClient, auth: dict[str, str], s: Setup, mega: FakeMega, delivered: list[int], db_session: Session
) -> None:
    db_session.get_one(CompanySettings, 1).action_mode = "advisory"
    card = db_session.scalars(select(ActionCard).where(ActionCard.kind == CardKind.INVOICE_DUE.value)).one()
    response = api.post(
        f"/api/v1/cards/{card.id}/actions/issue",
        json={"invoice_version": s.invoice.version},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    assert response.json()["proposal"] is not None
    assert db_session.get_one(Invoice, s.invoice_id).number is None
