"""@audited_task (agro-6p5.1): правилният актьор в audit_log, rollback при грешка."""

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit_context import clear_actor_cache
from app.models.audit import AuditLog
from app.models.identity import Actor
from app.workers import audited
from app.workers.audited import audited_task
from tests.audited_models import AuditedTestBase, Thing


@pytest.fixture
def session(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    AuditedTestBase.metadata.create_all(db_session.connection())
    clear_actor_cache()

    @contextmanager
    def fake_scope() -> Iterator[Session]:  # транзакцията на теста, без истински commit
        yield db_session

    monkeypatch.setattr(audited.db_session, "session_scope", fake_scope)
    yield db_session


@audited_task("system:scheduler", name="test.write_thing")
def write_thing(session: Session, name: str) -> int:
    thing = Thing(name=name)
    session.add(thing)
    session.flush()
    return thing.id


@audited_task("system:scheduler", name="test.fail")
def fail(session: Session) -> None:
    raise RuntimeError("boom")


def test_task_writes_audit_with_actor(session: Session) -> None:
    thing_id = write_thing.delay("a").get()
    log = session.scalars(select(AuditLog).where(AuditLog.entity_type == "test_things")).one()
    actor = session.scalar(select(Actor).where(Actor.id == log.actor_id))
    assert actor is not None and actor.code == "system:scheduler"
    assert log.entity_id == thing_id
    assert str(log.source) == "worker"


def test_unknown_actor_fails(session: Session) -> None:
    @audited_task("system:nope", name="test.unknown")
    def bad(s: Session) -> None:
        s.add(Thing(name="x"))

    with pytest.raises(LookupError):
        bad.delay().get()


def test_exception_propagates(session: Session) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        fail.delay().get()
