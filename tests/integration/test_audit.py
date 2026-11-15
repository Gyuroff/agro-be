"""Одит (docs/data-model.md §2, agro-l2v.2): контекст, слушатели, audit_log, тригер, record_bulk."""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.audit_context import (
    AuditContext,
    AuditSource,
    MissingAuditContext,
    actor_ctx_by_code,
    audit_context,
    clear_actor_cache,
    get_audit_context,
)
from app.db.audit import record_bulk
from app.models.audit import AuditLog
from app.models.identity import Actor
from tests.audited_models import AuditedTestBase, Event, Thing


@pytest.fixture
def session(db_session: Session) -> Iterator[Session]:
    AuditedTestBase.metadata.create_all(db_session.connection())
    yield db_session


def _logs(session: Session, entity_type: str = "test_things") -> list[AuditLog]:
    stmt = select(AuditLog).where(AuditLog.entity_type == entity_type).order_by(AuditLog.id)
    return list(session.scalars(stmt))


def _actor_id(session: Session, code: str) -> int:
    found = session.scalar(select(Actor.id).where(Actor.code == code))
    assert found is not None
    return found


def test_write_without_context_raises(session: Session) -> None:
    session.add(Thing(name="a"))
    with pytest.raises(MissingAuditContext):
        session.flush()


def test_get_audit_context_without_context_raises() -> None:
    with pytest.raises(MissingAuditContext):
        get_audit_context()


def test_context_is_reset_after_block(audit_ctx: AuditContext) -> None:
    other = AuditContext(actor_id=audit_ctx.actor_id, source=AuditSource.SHELL)
    with audit_context(other):
        assert get_audit_context() is other
    assert get_audit_context() is audit_ctx


def test_create_logs_all_columns_and_sets_actor(session: Session, audit_ctx: AuditContext) -> None:
    thing = Thing(name="a", price=Decimal("1.500"))
    session.add(thing)
    session.flush()
    assert thing.created_by_id == thing.updated_by_id == audit_ctx.actor_id
    (log,) = _logs(session)
    assert log.action == "create"
    assert log.entity_id == thing.id
    assert log.actor_id == audit_ctx.actor_id
    assert log.changes == {
        "id": [None, thing.id],
        "name": [None, "a"],
        "price": [None, "1.500"],
        "day": [None, None],
        "password_hash": [None, None],
    }


def test_update_logs_only_changed_field(session: Session, audit_ctx: AuditContext) -> None:
    thing = Thing(name="a", price=Decimal("1.000"))
    session.add(thing)
    session.flush()
    scheduler = actor_ctx_by_code("system:scheduler", AuditSource.WORKER, session=session)
    with audit_context(scheduler):
        thing.name = "b"
        session.flush()
    update = _logs(session)[-1]
    assert update.action == "update"
    assert update.changes == {"name": ["a", "b"]}
    assert update.actor_id == scheduler.actor_id
    assert thing.updated_by_id == scheduler.actor_id
    assert thing.created_by_id == audit_ctx.actor_id


def test_update_without_changes_writes_nothing(session: Session, audit_ctx: AuditContext) -> None:
    thing = Thing(name="a")
    session.add(thing)
    session.flush()
    thing.name = "a"
    session.flush()
    assert [log.action for log in _logs(session)] == ["create"]


def test_delete_logs_values(session: Session, audit_ctx: AuditContext) -> None:
    thing = Thing(name="a", day=date(2026, 11, 15))
    session.add(thing)
    session.flush()
    thing_id = thing.id
    session.delete(thing)
    session.flush()
    last = _logs(session)[-1]
    assert last.action == "delete"
    assert last.entity_id == thing_id
    assert last.changes["name"] == ["a", None]
    assert last.changes["day"] == ["2026-11-15", None]


def test_password_hash_is_masked(session: Session, audit_ctx: AuditContext) -> None:
    thing = Thing(name="a", password_hash="argon2id$secret")
    session.add(thing)
    session.flush()
    thing.password_hash = "argon2id$other"
    session.flush()
    created, updated = _logs(session)
    assert created.changes["password_hash"] == [None, "***"]
    assert updated.changes == {"password_hash": ["***", "***"]}
    assert "argon2id" not in str(created.changes) + str(updated.changes)


def test_decimal_and_date_serialization(session: Session, audit_ctx: AuditContext) -> None:
    thing = Thing(name="a", price=Decimal("24.860"), day=date(2026, 1, 31))
    session.add(thing)
    session.flush()
    (log,) = _logs(session)
    assert log.changes["price"] == [None, "24.860"]
    assert log.changes["day"] == [None, "2026-01-31"]


def test_request_id_source_and_reason_are_stored(session: Session, audit_ctx: AuditContext) -> None:
    ctx = AuditContext(audit_ctx.actor_id, AuditSource.API, request_id="req-42", reason="card:142 approve")
    with audit_context(ctx):
        session.add(Thing(name="a"))
        session.flush()
    (log,) = _logs(session)
    assert (log.source, log.request_id, log.reason) == ("api", "req-42", "card:142 approve")


def test_append_only_table_gets_created_columns_and_log(session: Session, audit_ctx: AuditContext) -> None:
    event = Event()
    session.add(event)
    session.flush()
    assert event.created_by_id == audit_ctx.actor_id
    (log,) = _logs(session, "test_events")
    assert log.action == "create"


def test_explicit_actor_overridden_unless_seed(session: Session, audit_ctx: AuditContext) -> None:
    other = _actor_id(session, "system:api")
    api_ctx = AuditContext(audit_ctx.actor_id, AuditSource.API)
    with audit_context(api_ctx):
        forced = Thing(name="x", created_by_id=other, updated_by_id=other)
        session.add(forced)
        session.flush()
    assert forced.created_by_id == audit_ctx.actor_id  # API не може да подмени актьора
    seeded = Thing(name="y", created_by_id=other, updated_by_id=other)
    session.add(seeded)  # audit_ctx е source=seed → изричната стойност се запазва
    session.flush()
    assert seeded.created_by_id == other


def test_audit_log_update_blocked_by_trigger(session: Session, audit_ctx: AuditContext) -> None:
    session.add(Thing(name="a"))
    session.flush()
    with pytest.raises(DBAPIError, match="audit_log is append-only"):
        session.execute(text("UPDATE audit_log SET reason = 'x'"))


def test_audit_log_delete_blocked_by_trigger(session: Session, audit_ctx: AuditContext) -> None:
    session.add(Thing(name="a"))
    session.flush()
    with pytest.raises(DBAPIError, match="audit_log is append-only"):
        session.execute(text("DELETE FROM audit_log"))


def test_actor_ctx_by_code_caches(session: Session) -> None:
    clear_actor_cache()
    first = actor_ctx_by_code("agent:report_matcher", AuditSource.AGENT, session=session)
    assert first.actor_id == _actor_id(session, "agent:report_matcher")
    # втори път — от кеша: работи и със счупена сесия
    broken: Any = None
    again = actor_ctx_by_code("agent:report_matcher", AuditSource.AGENT, request_id="r", session=broken)
    assert again.actor_id == first.actor_id
    assert again.request_id == "r"
    with pytest.raises(LookupError):
        actor_ctx_by_code("agent:nope", AuditSource.AGENT, session=session)


def test_record_bulk_writes_one_row_per_id(session: Session, audit_ctx: AuditContext) -> None:
    record_bulk(
        session,
        "trips",
        [7, 8],
        {"status": ("planned", "cancelled"), "password_hash": ("a", "b"), "price": (Decimal("1.10"), None)},
    )
    rows = _logs(session, "trips")
    assert [r.entity_id for r in rows] == [7, 8]
    assert rows[0].action == "update"
    assert rows[0].changes == {
        "status": ["planned", "cancelled"],
        "password_hash": ["***", "***"],
        "price": ["1.10", None],
    }


def test_record_bulk_requires_context(session: Session) -> None:
    with pytest.raises(MissingAuditContext):
        record_bulk(session, "trips", [1], {"status": ("a", "b")})


def test_excluded_tables_and_columns_constants() -> None:
    from app.db.audit import AUDIT_EXCLUDED_COLUMNS, AUDIT_EXCLUDED_TABLES

    assert {"audit_log", "mailbox_polls", "agent_runs", "push_notifications"} == AUDIT_EXCLUDED_TABLES
    assert AUDIT_EXCLUDED_COLUMNS["refresh_tokens"] == {"last_used_at"}


def test_bulk_delete_would_bypass_audit_documented(session: Session, audit_ctx: AuditContext) -> None:
    """Защо update()/delete() върху модели е забранено: заобикалят одита (виж test_no_bulk_dml)."""
    session.add(Thing(name="a"))
    session.flush()
    session.execute(delete(Thing))
    assert [log.action for log in _logs(session)] == ["create"]
