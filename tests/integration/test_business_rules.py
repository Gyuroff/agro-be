"""Добавки към integration suite „бизнес правила“ (testing.md §4, agro-br9.10).

Съответствие ID → тест (останалото живее в тематичните файлове):
T1–T3, T6 test_reports_api · T4/T5 test_trips_api · C1 test_trips · C2 test_contracts_api
C3 test_contracts_api · I1 test_domain_money_dates_billing · I2/I3/I4 test_invoicing
I5 test_invoice_issue · I6–I9 test_mega_comparison
R1–R3 test_result_api · A1/A2 test_audit · A3/A4 test_auth/test_admin · P1–P4 test_push
Тук: I10 и одитният ред при всеки преход на статус (критерий на задачата).
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit_context import AuditContext
from app.core.errors import DomainError
from app.models.audit import AuditLog
from app.models.company import CompanySettings
from app.models.invoicing import Invoice, InvoiceComparison
from app.models.trips import CancelSource
from app.services.invoicing import run_daily_billing
from app.services.mega_comparison import compare_mega_invoice, shadow_readiness
from app.services.trips import TripService
from tests.integration.test_mail_send import _env, company  # noqa: F401  (фикстури)
from tests.integration.test_mega_comparison import Setup, s  # noqa: F401  (фикстура)
from tests.integration.test_trips import World

D = Decimal


def _audit(session: Session, entity_type: str, entity_id: int) -> list[AuditLog]:
    session.flush()
    return list(
        session.scalars(
            select(AuditLog)
            .where(AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id)
            .order_by(AuditLog.id)
        )
    )


@pytest.fixture
def w(db_session: Session, audit_ctx: AuditContext) -> World:
    return World(db_session)


def test_trip_complete_is_audited(w: World, db_session: Session) -> None:
    trip = w.planned()
    TripService(db_session).complete(trip, actual_qty_t=D("24.860"), load_date=date(2026, 3, 5))
    last = _audit(db_session, "trips", trip.id)[-1]
    assert last.action == "update" and last.changes["status"] == ["planned", "completed"]
    assert last.actor_id is not None


def test_trip_cancel_is_audited(w: World, db_session: Session) -> None:
    trip = w.planned()
    TripService(db_session).cancel(trip, source=CancelSource.USER)
    last = _audit(db_session, "trips", trip.id)[-1]
    assert last.changes["status"] == ["planned", "cancelled"]


def test_forbidden_transition_writes_no_audit_row(w: World, db_session: Session) -> None:
    trip = w.planned()
    svc = TripService(db_session)
    svc.complete(trip, actual_qty_t=D("25"), load_date=date(2026, 3, 1))
    before = len(_audit(db_session, "trips", trip.id))
    with pytest.raises(DomainError) as err:
        svc.cancel(trip, source=CancelSource.USER)
    assert err.value.code == "invalid_transition"
    assert len(_audit(db_session, "trips", trip.id)) == before


def test_i10_ready_after_n_consecutive_matches(s: Setup, db_session: Session) -> None:  # noqa: F811
    required = db_session.scalars(select(CompanySettings.shadow_cycles_required)).one()
    cp = s.w.client.id
    assert required == 3 and shadow_readiness(db_session, cp) is False  # още няма сравнения

    assert compare_mega_invoice(db_session, s.mega()).result == "match"  # type: ignore[union-attr]
    assert shadow_readiness(db_session, cp) is False  # 1 от 3
    assert _compare(s, _next_cycle(s, date(2026, 11, 23), "СА 3001 ВХ"), "0000001525").result == "match"
    assert shadow_readiness(db_session, cp) is False  # 2 от 3
    assert _compare(s, _next_cycle(s, date(2026, 11, 30), "СА 3002 ВХ"), "0000001526").result == "match"
    assert shadow_readiness(db_session, cp) is True


def test_i10_mismatch_breaks_the_streak(s: Setup, db_session: Session) -> None:  # noqa: F811
    cp = s.w.client.id
    compare_mega_invoice(db_session, s.mega())
    _compare(s, _next_cycle(s, date(2026, 11, 23), "СА 3001 ВХ"), "0000001525", price="118.00")
    _compare(s, _next_cycle(s, date(2026, 11, 30), "СА 3002 ВХ"), "0000001526")
    assert shadow_readiness(db_session, cp) is False
    results = db_session.scalars(select(InvoiceComparison.result).order_by(InvoiceComparison.id)).all()
    assert list(results) == ["match", "mismatch", "match"]


def test_i10_flag_off_comparison_does_not_count(s: Setup, db_session: Session) -> None:  # noqa: F811
    cp = s.w.client.id
    first = compare_mega_invoice(db_session, s.mega())
    assert first is not None
    _compare(s, _next_cycle(s, date(2026, 11, 23), "СА 3001 ВХ"), "0000001525")
    _compare(s, _next_cycle(s, date(2026, 11, 30), "СА 3002 ВХ"), "0000001526")
    assert shadow_readiness(db_session, cp) is True
    first.counts_for_readiness = False
    db_session.flush()
    assert shadow_readiness(db_session, cp) is False


def _next_cycle(s: Setup, monday: date, plate: str) -> Invoice:  # noqa: F811
    """Следващ цикъл в shadow: един курс през седмицата → чернова на понеделник."""
    s.w.trip("24.870", monday - timedelta(days=6), plate=plate)
    (invoice_id,) = run_daily_billing(s.session, monday)
    return s.session.get_one(Invoice, invoice_id)


def _compare(s: Setup, invoice: Invoice, number: str, *, price: str = "120.00") -> InvoiceComparison:  # noqa: F811
    result = compare_mega_invoice(
        s.session, s.mega(price=price, qty="24.870", number=number, day=invoice.billing_date)
    )
    assert result is not None
    return result
