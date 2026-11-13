"""Общи dependencies. `CurrentUser` идва със задача agro-zmv.1."""

from collections.abc import Generator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.session import get_sessionmaker


def get_db() -> Generator[Session]:
    """Една сесия на заявка: commit след успех, rollback при изключение (вкл. DomainError).

    DomainError се хвърля от сервиза ПРЕДИ край на dependency → rollback и re-raise; глобалният
    handler (app/core/errors.py) връща JSON. Няма двоен commit (best-practices §B1).
    """
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db)]
