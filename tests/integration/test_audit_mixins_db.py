"""С база: оптимистично заключване и lazy="raise" върху истинска Postgres.

Тестовата таблица се създава в транзакцията на теста и изчезва с rollback-а (DDL в Postgres е
транзакционен). Одитните id-та идват от слушателя before_flush през фикстурата `audit_ctx`.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.core.audit_context import AuditContext
from tests.audited_models import AuditedTestBase, Thing


@pytest.fixture
def audited_session(db_session: Session, audit_ctx: AuditContext) -> Iterator[Session]:
    AuditedTestBase.metadata.create_all(
        db_session.connection(), tables=[AuditedTestBase.metadata.tables["test_things"]]
    )
    yield db_session


def _new_thing(session: Session) -> Thing:
    thing = Thing(name="a")
    session.add(thing)
    session.flush()
    return thing


def test_version_starts_at_1_and_increments(audited_session: Session) -> None:
    thing = _new_thing(audited_session)
    assert thing.version == 1
    thing.name = "b"
    audited_session.flush()
    assert thing.version == 2


def test_stale_update_raises(audited_session: Session) -> None:
    thing = _new_thing(audited_session)
    # друг процес е обновил реда междувременно
    audited_session.execute(
        text("UPDATE test_things SET version = version + 1 WHERE id = :id"), {"id": thing.id}
    )
    thing.name = "b"
    with pytest.raises(StaleDataError):
        audited_session.flush()


def test_created_by_is_lazy_raise(audited_session: Session) -> None:
    thing = _new_thing(audited_session)
    audited_session.expire(thing)
    loaded = audited_session.get(Thing, thing.id)
    assert loaded is not None
    with pytest.raises(InvalidRequestError):
        _ = loaded.created_by
