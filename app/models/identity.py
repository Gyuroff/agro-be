"""Актьори (docs/data-model.md §1) и потребители, устройства, сесии (§3)."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.db.base import AuditMixin, Base, enum_check


class ActorKind(StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class Actor(Base):
    """Един ред за всеки, който може да пише в базата: потребител, агент, системна задача.

    Без `created_by_id` (няма кокошка-яйце) — затова не ползва AuditMixin.
    """

    __tablename__ = "actors"
    __table_args__ = (
        enum_check("kind", ActorKind),
        CheckConstraint("kind <> 'user' OR user_id IS NOT NULL", name="user_requires_user_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", use_alter=True), nullable=True, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Platform(StrEnum):
    IOS = "ios"
    ANDROID = "android"


class PushProvider(StrEnum):
    FCM = "fcm"
    HMS = "hms"  # Huawei Push Kit
    NONE = "none"


class User(AuditMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    short_name: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    is_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    push_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    failed_login_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    locked_until: Mapped[datetime | None] = mapped_column(nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(nullable=True)

    @validates("email")
    def _normalize_email(self, _key: str, value: str) -> str:
        # data-model §0: имейлите се пазят lowercase, без интервали
        return value.strip().lower()


class Device(AuditMixin, Base):
    """Едно устройство = един телефон на потребител. Биометрията е на устройството."""

    __tablename__ = "devices"
    __table_args__ = (
        UniqueConstraint("user_id", "installation_id", name="user_installation"),
        enum_check("platform", Platform),
        enum_check("push_provider", PushProvider),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    installation_id: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    device_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    os_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    app_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    push_provider: Mapped[str] = mapped_column(
        Text, nullable=False, default=PushProvider.NONE.value, server_default=PushProvider.NONE.value
    )
    push_token: Mapped[str | None] = mapped_column(Text, nullable=True)  # маскира се в одита
    push_token_updated_at: Mapped[datetime | None] = mapped_column(nullable=True)
    biometric_enrolled_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)


class RefreshToken(AuditMixin, Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    device_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("devices.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)  # SHA-256 hex
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    rotated_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)
