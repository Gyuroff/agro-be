"""Вход в админа и одитен контекст (docs/data-model.md §2.1, §14).

`authenticate()` се вика на всяка админ заявка преди изгледа и задава одитния контекст
(actor на потребителя, source=admin). Контекстът не се връща назад: заявката е отделна задача
и contextvars не изтичат към други заявки; anyio го копира в нишката на sync операциите.
"""

import anyio
from sqladmin.authentication import AuthenticationBackend
from sqlalchemy import select
from starlette.requests import Request

from app.core.audit_context import AuditSource, actor_ctx_by_code, set_audit_context
from app.core.logging import get_request_id
from app.core.security import verify_dummy, verify_password
from app.db.session import get_sessionmaker
from app.models.identity import User

SESSION_KEY = "user_id"


def _find_user(email: str) -> User | None:
    with get_sessionmaker()() as session:
        return session.scalar(select(User).where(User.email == email.strip().lower()))


def _load_admin(user_id: int) -> User | None:
    with get_sessionmaker()() as session:
        user = session.get(User, user_id)
        return user if user is not None and user.is_active and user.is_admin else None


def _admin_actor_ctx(user_id: int):
    return actor_ctx_by_code(f"user:{user_id}", AuditSource.ADMIN, request_id=get_request_id())


class AdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        form = await request.form()
        email, password = str(form.get("username", "")), str(form.get("password", ""))
        user = await anyio.to_thread.run_sync(_find_user, email)
        if user is None:
            verify_dummy(password)  # еднакво време при непознат имейл
            return False
        if not verify_password(password, user.password_hash) or not user.is_active or not user.is_admin:
            return False
        request.session.clear()
        request.session[SESSION_KEY] = user.id
        return True

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        user_id = request.session.get(SESSION_KEY)
        if not isinstance(user_id, int):
            return False
        user = await anyio.to_thread.run_sync(_load_admin, user_id)
        if user is None:
            request.session.clear()
            return False
        ctx = await anyio.to_thread.run_sync(_admin_actor_ctx, user.id)
        set_audit_context(ctx)
        return True
