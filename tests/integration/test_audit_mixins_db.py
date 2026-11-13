"""С база: оптимистично заключване и lazy="raise" върху истинска Postgres.

Тестовата таблица се създава в транзакцията на теста и изчезва с rollback-а (DDL в Postgres е
транзакционен). Одитните id-та се задават ръчно — слушателят before_flush идва с agro-l2v.2.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from tests.audited_models import Actor, AuditedTestBase, Thing


@pytest.fixture
def audited_session(db_session: Session) -> Iterator[Session]:
    AuditedTestBase.metadata.create_all(
        db_session.connection(), tables=[AuditedTestBase.metadata.tables["test_things"]]
    )
    yield db_session


def _seed_actor_id(session: Session) -> int:
    actor_id = session.scalar(select(Actor.id).where(Actor.code == "system:seed"))
    assert actor_id is not None
    return actor_id


def _new_thing(session: Session) -> Thing:
    actor_id = _seed_actor_id(session)
    thing = Thing(name="a", created_by_id=actor_id, updated_by_id=actor_id)
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
