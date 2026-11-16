"""Вход, refresh, изход (docs/api.md §1, docs/data-model.md §3).

Провалите, след които състоянието трябва да се запази (брояч, заключване, reuse detection), се връщат като
`AuthFailure`, а не се хвърлят: `get_db` прави rollback при изключение. Роутерът ги превръща в отговор.
Записите са flush-нати вътре в одитния контекст (commit е в `get_db`).
"""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.audit_context import AuditContext, AuditSource, actor_ctx_by_code, audit_context
from app.core.errors import DomainError
from app.core.logging import get_request_id
from app.core.security import (
    ACCESS_TOKEN_TTL,
    REFRESH_TOKEN_TTL,
    create_access_token,
    hash_password,
    hash_refresh_token,
    needs_rehash,
    new_refresh_token,
    verify_dummy,
    verify_password,
)
from app.models.identity import Device, RefreshToken, User

MAX_FAILED_LOGINS = 10
LOCK_DURATION = timedelta(minutes=15)


@dataclass(frozen=True)
class AuthFailure:
    """Провал, който трябва да се commit-не (брояч, отнети токени), преди да се върне грешката."""

    code: str
    message_bg: str
    http_status: int


@dataclass(frozen=True)
class Tokens:
    access_token: str
    access_expires_in: int
    refresh_token: str


@dataclass(frozen=True)
class LoginResult:
    user: User
    device: Device
    tokens: Tokens


_INVALID_CREDENTIALS = AuthFailure("invalid_credentials", "Грешен имейл или парола.", 401)
_TOKEN_INVALID = AuthFailure("token_invalid", "Невалидна сесия.", 401)


def user_audit_ctx(session: Session, user_id: int) -> AuditContext:
    return actor_ctx_by_code(f"user:{user_id}", AuditSource.API, request_id=get_request_id(), session=session)


def _system_ctx(session: Session) -> AuditContext:
    return actor_ctx_by_code("system:api", AuditSource.API, request_id=get_request_id(), session=session)


def _issue_tokens(session: Session, user: User, device: Device) -> Tokens:
    plain, token_hash = new_refresh_token()
    session.add(
        RefreshToken(device_id=device.id, token_hash=token_hash, expires_at=clock.now() + REFRESH_TOKEN_TTL)
    )
    session.flush()
    return Tokens(
        access_token=create_access_token(user.id, device.id),
        access_expires_in=int(ACCESS_TOKEN_TTL.total_seconds()),
        refresh_token=plain,
    )


def _revoke_device_tokens(session: Session, device_id: int) -> None:
    now = clock.now()
    tokens = session.scalars(
        select(RefreshToken).where(RefreshToken.device_id == device_id, RefreshToken.revoked_at.is_(None))
    )
    for token in tokens:
        token.revoked_at = now


class AuthService:
    @staticmethod
    def login(
        session: Session,
        *,
        email: str,
        password: str,
        installation_id: str,
        platform: str,
        device_model: str | None = None,
        os_version: str | None = None,
        app_version: str | None = None,
    ) -> LoginResult | AuthFailure:
        now = clock.now()
        user = session.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None:
            verify_dummy(password)
            return _INVALID_CREDENTIALS

        if user.locked_until is not None and user.locked_until > now:
            raise DomainError("account_locked", "Профилът е заключен. Опитайте по-късно.", 423)

        # До потвърждаване на паролата потребителят не е удостоверен → system:api.
        with audit_context(_system_ctx(session)):
            if not verify_password(password, user.password_hash):
                if user.locked_until is not None:  # изтекло заключване → започваме броенето отначало
                    user.locked_until = None
                    user.failed_login_count = 0
                user.failed_login_count += 1
                if user.failed_login_count >= MAX_FAILED_LOGINS:
                    user.locked_until = now + LOCK_DURATION
                session.flush()
                return _INVALID_CREDENTIALS

        if not user.is_active:
            return AuthFailure("user_inactive", "Профилът е неактивен.", 401)

        with audit_context(user_audit_ctx(session, user.id)):
            user.failed_login_count = 0
            user.locked_until = None
            user.last_login_at = now
            if needs_rehash(user.password_hash):
                user.password_hash = hash_password(password)

            device = session.scalar(
                select(Device).where(Device.user_id == user.id, Device.installation_id == installation_id)
            )
            if device is None:
                device = Device(user_id=user.id, installation_id=installation_id, platform=platform)
                session.add(device)
            device.platform = platform
            device.device_model = device_model
            device.os_version = os_version
            device.app_version = app_version
            device.last_seen_at = now
            device.revoked_at = None  # нов вход на отнето устройство го връща в употреба
            session.flush()
            tokens = _issue_tokens(session, user, device)
        return LoginResult(user=user, device=device, tokens=tokens)

    @staticmethod
    def refresh(session: Session, *, refresh_token: str) -> Tokens | AuthFailure:
        now = clock.now()
        token = session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(refresh_token))
        )
        if token is None:
            return _TOKEN_INVALID
        device = session.get(Device, token.device_id)
        assert device is not None
        user = session.get(User, device.user_id)
        assert user is not None

        if token.rotated_at is not None:
            # Повторна употреба на ротиран токен → кражба? Отнемаме всичко на устройството.
            with audit_context(_system_ctx(session)):
                _revoke_device_tokens(session, device.id)
                session.flush()
            return _TOKEN_INVALID
        if (
            token.revoked_at is not None
            or token.expires_at <= now
            or device.revoked_at is not None
            or not user.is_active
        ):
            return _TOKEN_INVALID

        with audit_context(user_audit_ctx(session, user.id)):
            token.rotated_at = now
            token.last_used_at = now
            device.last_seen_at = now
            session.flush()
            return _issue_tokens(session, user, device)

    @staticmethod
    def logout(session: Session, *, device: Device) -> None:
        """Отнема устройството и всичките му refresh токени. Извиква се в контекста на потребителя."""
        device.revoked_at = clock.now()
        _revoke_device_tokens(session, device.id)
        session.flush()
