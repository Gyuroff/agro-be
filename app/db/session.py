"""Engine и сесии (sync SQLAlchemy — умишлено, docs/architecture.md §2).

Commit е ТОЛКОВА на едно място: dependency `get_db` (API) и `session_scope` (Celery, скриптове).
Сервизите само `flush()`.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Транзакция за Celery задачи и скриптове: commit при успех, rollback при изключение."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_db_caches() -> None:
    """За тестове: след смяна на настройките (get_settings.cache_clear())."""
    get_sessionmaker.cache_clear()
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_engine.cache_clear()
