"""Общи dependencies: сесия на заявка и `CurrentPrincipal` (JWT + одитен контекст)."""

from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.audit_context import audit_context
from app.core.errors import DomainError
from app.core.security import decode_access_token
from app.db.session import get_sessionmaker
from app.models.identity import Device, User
from app.services.auth import user_audit_ctx


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

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    user: User
    device: Device


def _load_principal(session: Session, token: str) -> Principal:
    claims = decode_access_token(token)
    user = session.get(User, claims.user_id)
    device = session.get(Device, claims.device_id)
    if user is None or device is None or device.user_id != user.id or device.revoked_at is not None:
        raise DomainError("token_invalid", "Невалидна сесия.", 401)
    if not user.is_active:
        raise DomainError("user_inactive", "Профилът е неактивен.", 401)
    return Principal(user=user, device=device)


async def current_principal(
    session: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AsyncGenerator[Principal]:
    """Валидира JWT, устройството и потребителя и задава одитния контекст (потребителят, source=api).

    Async, за да се види контекстът от endpoint-а (синхронна dependency би го задала в друга нишка).
    Преди да излезе от контекста, flush-ва: commit-ът на `get_db` е след него и иначе би гърмял.
    """
    if credentials is None:
        raise DomainError("token_invalid", "Липсва сесия.", 401)
    principal = await run_in_threadpool(_load_principal, session, credentials.credentials)
    ctx = await run_in_threadpool(user_audit_ctx, session, principal.user.id)
    with audit_context(ctx):
        yield principal
        await run_in_threadpool(session.flush)


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]
