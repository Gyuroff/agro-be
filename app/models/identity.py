"""Актьори (docs/data-model.md §1).

`users`, `devices`, `refresh_tokens` идват със задача agro-l2v.3.
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, enum_check


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
    # FK към users(id) се добавя от миграцията на agro-l2v.3 (users още не съществува).
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
