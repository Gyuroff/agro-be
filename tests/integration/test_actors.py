import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.identity import Actor

EXPECTED_CODES = {
    "system:migration",
    "system:seed",
    "system:scheduler",
    "system:mail_ingest",
    "system:api",
    "agent:email_classifier",
    "agent:request_parser",
    "agent:report_parser",
    "agent:report_matcher",
    "agent:cancellation_parser",
    "agent:mi_invoice_parser",
    "agent:invoice_comparator",
}


def test_seed_actors_present(db_session: Session) -> None:
    codes = set(db_session.scalars(select(Actor.code)).all())
    assert codes >= EXPECTED_CODES


def test_seed_kinds(db_session: Session) -> None:
    by_code = {a.code: a.kind for a in db_session.scalars(select(Actor)).all()}
    assert by_code["system:scheduler"] == "system"
    assert by_code["agent:report_matcher"] == "agent"


def test_invalid_kind_rejected(db_session: Session) -> None:
    db_session.add(Actor(kind="robot", code="x:1", display_name="X"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_user_actor_requires_user_id(db_session: Session) -> None:
    db_session.add(Actor(kind="user", code="user:1", display_name="Георги"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_code_is_unique(db_session: Session) -> None:
    db_session.add(Actor(kind="system", code="system:seed", display_name="Дубликат"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_rollback_isolates_tests(db_session: Session) -> None:
    # предишните тестове не оставят следа (транзакция + rollback)
    assert db_session.scalar(select(Actor).where(Actor.code == "x:1")) is None
