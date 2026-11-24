"""Режим сянка (agro-66f.4): сравнение с фактурата от Мега, карти, mega_missing_check, API."""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.audit_context import AuditContext
from app.main import create_app
from app.models.cards import ActionCard, CardKind, CardResolution, CardStatus
from app.models.company import CompanySettings
from app.models.invoicing import (
    ComparisonResult,
    Invoice,
    InvoiceComparison,
    MegaInvoice,
    MegaInvoiceSource,
)
from app.models.mail import OutboundEmail, OutboundPurpose, OutboundStatus
from app.models.trips import Trip, TripEvent
from app.services.invoicing import run_daily_billing
from app.services.mega_comparison import compare_mega_invoice, mega_missing_check
from app.services.users import UserService
from tests.integration.test_auth import EMAIL, PASSWORD, bearer, login
from tests.integration.test_invoicing import World
from tests.integration.test_mail_send import _env, company  # noqa: F401  (фикстури)

D = Decimal
MONDAY = date(2026, 11, 16)


class Setup:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.w = World(session, mode="shadow")
        self.trips = self.w.figma_week()
        (self.invoice_id,) = run_daily_billing(session, MONDAY)
        self.invoice = session.get_one(Invoice, self.invoice_id)

    def mega(
        self, *, price: str = "120.00", qty: str = "273.760", number: str = "0000001524", day: date = MONDAY
    ) -> MegaInvoice:
        amount = (D(qty) * D(price)).quantize(D("0.01"))
        vat = (amount * D("0.2")).quantize(D("0.01"))
        m = MegaInvoice(
            source=MegaInvoiceSource.EMAIL_COPY.value,
            number=number,
            issue_date=day,
            counterparty_id=self.w.client.id,
            recipient_eik=self.w.client.eik,
            lines=[{"description": "Пелети", "qty_t": qty, "unit_price": price, "amount": str(amount)}],
            subtotal=amount,
            vat_amount=vat,
            total=amount + vat,
        )
        self.session.add(m)
        self.session.flush()
        return m


@pytest.fixture
def s(db_session: Session, audit_ctx: AuditContext, company: CompanySettings) -> Setup:  # noqa: F811
    return Setup(db_session)


def kinds_cards(session: Session, kind: CardKind) -> list[ActionCard]:
    return list(session.scalars(select(ActionCard).where(ActionCard.kind == kind.value)))


def test_match_marks_trips_invoiced_without_card(s: Setup, db_session: Session) -> None:
    comparison = compare_mega_invoice(db_session, s.mega())
    assert comparison is not None and comparison.result == ComparisonResult.MATCH.value
    assert comparison.diff_kinds == []
    trips = db_session.scalars(select(Trip).where(Trip.id.in_([t.id for t in s.trips]))).all()
    assert {t.invoice_id for t in trips} == {s.invoice_id}
    events = db_session.scalars(select(TripEvent).where(TripEvent.text.like("%0000001524%"))).all()
    assert len(events) == 11
    assert kinds_cards(db_session, CardKind.INVOICE_MISMATCH) == []
    assert s.invoice.status == "draft"  # в shadow черновата си остава чернова


def test_rounding_within_tolerance_is_match(s: Setup, db_session: Session) -> None:
    mega = s.mega()
    assert mega.total is not None
    mega.total += D("0.01")
    comparison = compare_mega_invoice(db_session, mega)
    assert comparison is not None and comparison.result == "match"
    assert comparison.diff_kinds == ["rounding"]


def test_mismatch_creates_card_and_figma_rows(s: Setup, db_session: Session) -> None:
    comparison = compare_mega_invoice(db_session, s.mega(price="118.00"))
    assert comparison is not None and comparison.result == "mismatch"
    assert comparison.diff_kinds == ["price"]
    (card,) = kinds_cards(db_session, CardKind.INVOICE_MISMATCH)
    assert card.invoice_comparison_id == comparison.id and card.invoice_id == s.invoice_id
    assert card.payload["difference"] == "-547.52"
    assert card.payload["mega_number"] == "0000001524"
    assert card.payload["rows"][2] == {"label": "Цена / т", "erp": "120.00", "mega": "118.00"}
    assert card.payload["rows"][3] == {"label": "Без ДДС", "erp": "32851.20", "mega": "32303.68"}
    assert "118.00" in comparison.agent_explanation  # type: ignore[operator]
    trips = db_session.scalars(select(Trip).where(Trip.id.in_([t.id for t in s.trips]))).all()
    assert {t.invoice_id for t in trips} == {None}  # до решението курсовете не са „Фактуриран“


def test_closest_draft_when_period_does_not_cover(s: Setup, db_session: Session) -> None:
    comparison = compare_mega_invoice(db_session, s.mega(day=date(2026, 11, 25)))
    assert comparison is not None and comparison.invoice_id == s.invoice_id


def test_unknown_customer_or_no_draft_gives_nothing(s: Setup, db_session: Session) -> None:
    mega = s.mega()
    mega.counterparty_id = None
    mega.recipient_eik = "999999999"
    assert compare_mega_invoice(db_session, mega) is None


def test_missing_after_two_working_days(s: Setup, db_session: Session) -> None:
    assert mega_missing_check(db_session, date(2026, 11, 17)) == []
    (cid,) = mega_missing_check(db_session, date(2026, 11, 18))
    comparison = db_session.get_one(InvoiceComparison, cid)
    assert comparison.result == "missing" and comparison.mega_invoice_id is None
    (card,) = kinds_cards(db_session, CardKind.INVOICE_MISSING)
    assert card.payload["subtotal"] == "32851.20" and card.invoice_id == s.invoice_id
    assert mega_missing_check(db_session, date(2026, 11, 19)) == []  # без дубликат


def test_holiday_in_between_makes_it_three_calendar_days(s: Setup, db_session: Session) -> None:
    s.invoice.billing_date = date(2026, 5, 4)  # понеделник; 6 май (Гергьовден) е празник
    db_session.flush()
    assert mega_missing_check(db_session, date(2026, 5, 6)) == []
    assert mega_missing_check(db_session, date(2026, 5, 7)) != []


def test_late_mega_invoice_supersedes_missing_card(s: Setup, db_session: Session) -> None:
    mega_missing_check(db_session, date(2026, 11, 18))
    comparison = compare_mega_invoice(db_session, s.mega())
    assert comparison is not None and comparison.result == "match"
    (card,) = kinds_cards(db_session, CardKind.INVOICE_MISSING)
    assert card.status == CardStatus.SUPERSEDED.value
    assert db_session.scalars(select(InvoiceComparison)).all() == [comparison]


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


def test_api_get_comparison_returns_figma_rows(
    api: TestClient, auth: dict[str, str], s: Setup, db_session: Session
) -> None:
    comparison = compare_mega_invoice(db_session, s.mega(price="118.00"))
    assert comparison is not None
    body = api.get(f"/api/v1/comparisons/{comparison.id}", headers=auth).json()
    assert body["customer_name"] == "Агро Трейд" and body["mega_number"] == "0000001524"
    assert body["resolution"] is None
    by_label = {r["label"]: r for r in body["rows"]}
    assert by_label["Тонаж"] == {
        "label": "Тонаж",
        "erp": "273.760",
        "mega": "273.760",
        "diff": None,
        "ok": True,
    }
    assert by_label["Цена / т"] == {
        "label": "Цена / т", "erp": "120.00", "mega": "118.00", "diff": "-2.00", "ok": False
    }  # fmt: skip
    assert by_label["Без ДДС"]["diff"] == "-547.52" and by_label["Без ДДС"]["ok"] is False
    assert api.get("/api/v1/comparisons/999999", headers=auth).status_code == 404


def test_resolve_notify_accountant_prepares_draft_only(
    api: TestClient, auth: dict[str, str], s: Setup, db_session: Session
) -> None:
    db_session.get_one(CompanySettings, 1).accountant_email = "acc@example.bg"
    comparison = compare_mega_invoice(db_session, s.mega(price="118.00"))
    assert comparison is not None
    response = api.post(
        f"/api/v1/comparisons/{comparison.id}/resolve", json={"resolution": "notify_accountant"}, headers=auth
    )
    assert response.status_code == 200, response.text
    assert response.json()["resolution"] == "notify_accountant"
    note = db_session.scalars(
        select(OutboundEmail).where(OutboundEmail.purpose == OutboundPurpose.ACCOUNTANT_NOTE.value)
    ).one()
    assert note.status == OutboundStatus.DRAFT.value and note.to_addresses == ["acc@example.bg"]
    assert "118.00" in note.body_text
    (card,) = kinds_cards(db_session, CardKind.INVOICE_MISMATCH)
    assert (card.status, card.resolution) == (CardStatus.DONE.value, CardResolution.NOTIFY_ACCOUNTANT.value)
    again = api.post(
        f"/api/v1/comparisons/{comparison.id}/resolve", json={"resolution": "mega_correct"}, headers=auth
    )
    assert again.status_code == 409


def test_card_action_mega_correct_marks_trips(
    api: TestClient, auth: dict[str, str], s: Setup, db_session: Session
) -> None:
    comparison = compare_mega_invoice(db_session, s.mega(price="118.00"))
    assert comparison is not None
    (card,) = kinds_cards(db_session, CardKind.INVOICE_MISMATCH)
    response = api.post(f"/api/v1/cards/{card.id}/actions/mega_correct", json={}, headers=auth)
    assert response.status_code == 200, response.text
    db_session.refresh(card)
    db_session.refresh(comparison)
    assert (card.status, card.resolution) == (CardStatus.DONE.value, CardResolution.MEGA_CORRECT.value)
    assert comparison.resolution == "mega_correct" and comparison.resolved_at is not None
    trips = db_session.scalars(select(Trip).where(Trip.id.in_([t.id for t in s.trips]))).all()
    assert {t.invoice_id for t in trips} == {s.invoice_id}
