"""users/devices/refresh_tokens/company_settings/number_sequences (agro-l2v.3)."""

import threading
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit_context import AuditContext, AuditSource, audit_context
from app.models.company import CompanySettings, NumberSequence
from app.models.identity import Actor, Device
from app.services.numbering import NumberRangeExhausted, format_number, take_next
from app.services.users import UserService
from tests.conftest import TEST_DATABASE_URL


def _seq(session: Session, code: str) -> NumberSequence:
    return session.scalars(select(NumberSequence).where(NumberSequence.code == code)).one()


def test_migration_seeds(db_session: Session) -> None:
    company = db_session.get(CompanySettings, 1)
    assert company is not None
    assert company.legal_name == "Агро Импорт Трейдинг ООД"
    assert company.default_vat_rate == Decimal("20.00")
    assert company.default_planned_truck_qty_t == Decimal("25.200")
    assert (company.push_quiet_from.hour, company.push_quiet_to.hour) == (21, 7)
    assert _seq(db_session, "invoice_main").next_number == 1
    pilot = _seq(db_session, "invoice_pilot")
    assert (pilot.range_start, pilot.range_end) == (9000000001, 9000009999)
    assert _seq(db_session, "email_ref").next_number == 1


def test_second_company_settings_row_rejected(db_session: Session, audit_ctx: AuditContext) -> None:
    db_session.add(
        CompanySettings(
            id=2,
            legal_name="X",
            short_name="X",
            eik="1",
            address_city="",
            address_line="",
            mol="",
            email_signature="",
            system_mailbox_address="",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_take_next_sequential(db_session: Session, audit_ctx: AuditContext) -> None:
    assert [take_next(db_session, "email_ref") for _ in range(3)] == [1, 2, 3]
    assert _seq(db_session, "email_ref").next_number == 4


def test_format_number() -> None:
    assert format_number(1525, 10) == "0000001525"
    assert format_number(142, 4) == "0142"


def test_range_exhausted(db_session: Session, audit_ctx: AuditContext) -> None:
    seq = _seq(db_session, "invoice_pilot")
    seq.next_number = 9000009998
    db_session.flush()
    assert take_next(db_session, "invoice_pilot") == 9000009998
    assert take_next(db_session, "invoice_pilot") == 9000009999
    with pytest.raises(NumberRangeExhausted):
        take_next(db_session, "invoice_pilot")


def test_unknown_sequence(db_session: Session, audit_ctx: AuditContext) -> None:
    with pytest.raises(LookupError):
        take_next(db_session, "nope")


def test_user_create_creates_actor(db_session: Session, audit_ctx: AuditContext) -> None:
    user = UserService.create(
        db_session,
        email="  Georgi@Example.COM ",
        full_name="Георги Гюров",
        short_name="Георги",
        password="s3cret-pass",
        is_admin=True,
    )
    assert user.email == "georgi@example.com"
    assert user.password_hash.startswith("$argon2id$")
    assert user.is_active and user.push_enabled and user.failed_login_count == 0
    actor = db_session.scalars(select(Actor).where(Actor.user_id == user.id)).one()
    assert (actor.kind, actor.code, actor.display_name) == ("user", f"user:{user.id}", "Георги")


def test_user_email_unique(db_session: Session, audit_ctx: AuditContext) -> None:
    UserService.create(db_session, email="a@x.bg", full_name="A", short_name="A", password="pw-pw-pw-1")
    with pytest.raises(IntegrityError):
        UserService.create(db_session, email="A@X.bg", full_name="B", short_name="B", password="pw-pw-pw-2")


def test_device_constraints(db_session: Session, audit_ctx: AuditContext) -> None:
    user = UserService.create(
        db_session, email="d@x.bg", full_name="D", short_name="D", password="pw-pw-pw-1"
    )
    db_session.add(Device(user_id=user.id, installation_id="i-1", platform="ios"))
    db_session.flush()
    with db_session.begin_nested():
        db_session.add(Device(user_id=user.id, installation_id="i-1", platform="android"))
        with pytest.raises(IntegrityError):
            db_session.flush()
    with db_session.begin_nested():
        db_session.add(Device(user_id=user.id, installation_id="i-2", platform="windows"))
        with pytest.raises(IntegrityError):
            db_session.flush()


def test_device_push_token_masked_in_audit(db_session: Session, audit_ctx: AuditContext) -> None:
    from app.models.audit import AuditLog

    user = UserService.create(
        db_session, email="m@x.bg", full_name="M", short_name="M", password="pw-pw-pw-1"
    )
    device = Device(user_id=user.id, installation_id="i-1", platform="ios", push_token="secret-token")
    db_session.add(device)
    db_session.flush()
    log = db_session.scalars(
        select(AuditLog).where(AuditLog.entity_type == "devices", AuditLog.entity_id == device.id)
    ).one()
    assert "secret-token" not in str(log.changes)
    user_log = db_session.scalars(
        select(AuditLog).where(AuditLog.entity_type == "users", AuditLog.entity_id == user.id)
    ).one()
    assert "argon2" not in str(user_log.changes)


def test_concurrent_take_next_distinct() -> None:
    """Две сесии, втората чака заключването на първата и получава следващия номер."""
    engine = create_engine(TEST_DATABASE_URL)
    results: list[int] = []
    first_holds = threading.Event()
    release_first = threading.Event()

    def run(hold: bool) -> None:
        with Session(engine) as s:
            actor_id = s.scalar(select(Actor.id).where(Actor.code == "system:seed"))
            assert actor_id is not None
            with audit_context(AuditContext(actor_id, AuditSource.SEED)):
                results.append(take_next(s, "email_ref"))
                if hold:
                    first_holds.set()
                    release_first.wait(5)
                s.commit()

    with Session(engine) as s:
        before = s.scalar(select(NumberSequence.next_number).where(NumberSequence.code == "email_ref"))
    assert before is not None
    t1 = threading.Thread(target=run, args=(True,))
    t1.start()
    assert first_holds.wait(5)
    t2 = threading.Thread(target=run, args=(False,))
    t2.start()
    t2.join(0.5)
    assert t2.is_alive()  # чака FOR UPDATE
    release_first.set()
    t1.join(5)
    t2.join(5)
    try:
        assert sorted(results) == [before, before + 1]
    finally:
        with engine.begin() as conn:  # връщаме номерацията (тестът комитва извън транзакцията на фикстурата)
            conn.execute(text("ALTER TABLE number_sequences DISABLE TRIGGER ALL"))
            conn.execute(
                text("UPDATE number_sequences SET next_number = :n WHERE code = 'email_ref'"), {"n": before}
            )
            conn.execute(text("ALTER TABLE number_sequences ENABLE TRIGGER ALL"))
        engine.dispose()
