"""Пароли и токени (docs/data-model.md §3, docs/api.md §1).

Пароли: argon2id. Access token: JWT HS256, 15 мин. Refresh token: непрозрачен, в базата е само SHA-256.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core import clock
from app.core.config import get_settings
from app.core.errors import DomainError

ACCESS_TOKEN_TTL = timedelta(minutes=15)
REFRESH_TOKEN_TTL = timedelta(days=30)
REFRESH_TOKEN_PREFIX = "r_"

_hasher = PasswordHasher()
# Хеш за фиктивна проверка при непознат имейл (еднакво време на отговора).
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def verify_dummy(password: str) -> None:
    verify_password(password, _DUMMY_HASH)


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@dataclass(frozen=True)
class AccessClaims:
    user_id: int
    device_id: int
    jti: str


def create_access_token(user_id: int, device_id: int) -> str:
    now = clock.now()
    payload = {
        "sub": str(user_id),  # JWT изисква низ
        "did": device_id,
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, get_settings().jwt_secret.get_secret_value(), algorithm="HS256")


def decode_access_token(token: str) -> AccessClaims:
    """Валидира подпис и срок. Изтекъл → `token_expired`, всичко останало → `token_invalid` (и двата 401)."""
    try:
        data = jwt.decode(
            token,
            get_settings().jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            options={"require": ["sub", "did", "iat", "exp", "jti"]},
        )
        return AccessClaims(user_id=int(data["sub"]), device_id=int(data["did"]), jti=str(data["jti"]))
    except jwt.ExpiredSignatureError:
        raise DomainError("token_expired", "Сесията е изтекла.", 401) from None
    except (jwt.InvalidTokenError, ValueError, TypeError):
        raise DomainError("token_invalid", "Невалидна сесия.", 401) from None


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_refresh_token() -> tuple[str, str]:
    """(plain, sha256). Само hash-ът влиза в базата."""
    plain = REFRESH_TOKEN_PREFIX + secrets.token_urlsafe(32)
    return plain, hash_refresh_token(plain)
