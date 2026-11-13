"""get_db: commit при успех, rollback при изключение (best-practices §B1)."""

from unittest.mock import MagicMock

import pytest

from app.api import deps
from app.core.errors import DomainError


def _patch_session(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    session = MagicMock()
    monkeypatch.setattr(deps, "get_sessionmaker", lambda: lambda: session)
    return session


def test_commit_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _patch_session(monkeypatch)
    gen = deps.get_db()
    assert next(gen) is session
    with pytest.raises(StopIteration):
        next(gen)
    session.commit.assert_called_once()
    session.rollback.assert_not_called()
    session.close.assert_called_once()


def test_rollback_on_domain_error(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _patch_session(monkeypatch)
    gen = deps.get_db()
    next(gen)
    with pytest.raises(DomainError):
        gen.throw(DomainError("card_not_open", "Картата вече е обработена.", 409))
    session.commit.assert_not_called()
    session.rollback.assert_called_once()
    session.close.assert_called_once()
