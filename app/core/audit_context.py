"""Одитен контекст (docs/data-model.md §2.1): кой и откъде пише в базата.

Без контекст записът гърми с `MissingAuditContext` — умишлено, няма запис без „кой“.
Контекстът се задава на входните точки (API dependency, админ, Celery, агент, сийд), не в сервизите.
"""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.orm import Session


class AuditSource(StrEnum):
    API = "api"
    ADMIN = "admin"
    WORKER = "worker"
    AGENT = "agent"
    SEED = "seed"
    MIGRATION = "migration"
    SHELL = "shell"


class MissingAuditContext(RuntimeError):
    """Запис в одитирана таблица извън `audit_context(...)`. Оправи входната точка, не проверката."""


@dataclass(frozen=True)
class AuditContext:
    actor_id: int
    source: AuditSource
    request_id: str | None = None
    reason: str | None = None  # свободен текст, напр. "card:142 approve"


_current: ContextVar[AuditContext | None] = ContextVar("audit_ctx", default=None)


@contextmanager
def audit_context(ctx: AuditContext) -> Generator[AuditContext]:
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)


def set_audit_context(ctx: AuditContext) -> None:
    """Задава контекста за останалата част от текущата заявка/задача (без връщане назад).

    Само за входни точки, които нямат `with`-обхват около изгледа (SQLAdmin `authenticate`).
    """
    _current.set(ctx)


def get_audit_context() -> AuditContext:
    ctx = _current.get()
    if ctx is None:
        raise MissingAuditContext("Липсва одитен контекст: обвий записа в audit_context(...).")
    return ctx


# code → actors.id. Редовете на actors не се трият и не променят кода си, затова кешът е безопасен.
_ACTOR_IDS: dict[str, int] = {}


def clear_actor_cache() -> None:
    """За тестове."""
    _ACTOR_IDS.clear()


def actor_ctx_by_code(
    code: str,
    source: AuditSource,
    *,
    request_id: str | None = None,
    reason: str | None = None,
    session: Session | None = None,
) -> AuditContext:
    """Контекст за актьор по код (`system:scheduler`, `agent:report_matcher`). Кешира id-тата.

    Без `session` отваря кратка собствена сесия само за четене при пропуск в кеша.
    """
    actor_id = _ACTOR_IDS.get(code)
    if actor_id is None:
        actor_id = _lookup_actor_id(code, session)
        _ACTOR_IDS[code] = actor_id
    return AuditContext(actor_id=actor_id, source=source, request_id=request_id, reason=reason)


def _lookup_actor_id(code: str, session: Session | None) -> int:
    # Локални импорти: app.core не трябва да зависи от моделите при импорт (кръгове).
    from sqlalchemy import select

    from app.db.session import get_sessionmaker
    from app.models.identity import Actor

    stmt = select(Actor.id).where(Actor.code == code)
    if session is not None:
        found = session.scalar(stmt)
    else:
        with get_sessionmaker()() as own:
            found = own.scalar(stmt)
    if found is None:
        raise LookupError(f"Непознат актьор: {code!r}")
    return found
