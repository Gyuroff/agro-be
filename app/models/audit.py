"""audit_log — само добавяне (docs/data-model.md §2.2).

Колоните са по §2.2 (`occurred_at`, `actor_id`), не `CreatedMixin` от §0.1: таблицата не се пише
през сесията, а от слушателя `after_flush` (app/db/audit.py), и тригерът я прави неизменяема.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.audit_context import AuditSource
from app.db.base import Base, enum_check
from app.models.enums import AuditAction


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        enum_check("source", AuditSource),
        enum_check("action", AuditAction),
        Index("ix_audit_log_entity", "entity_type", "entity_id", text("occurred_at DESC")),
        Index("ix_audit_log_actor", "actor_id", text("occurred_at DESC")),
        Index("ix_audit_log_occurred_at", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    actor_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("actors.id"), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    changes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
