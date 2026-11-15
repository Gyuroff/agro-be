"""Импортът на всички модели регистрира таблиците в Base.metadata (Alembic autogenerate)."""

from app.models.audit import AuditLog
from app.models.identity import Actor
from app.models.storage import StoredObject

__all__ = ["Actor", "AuditLog", "StoredObject"]
