"""Общи enum-и на моделите (docs/data-model.md §0: StrEnum в Python, TEXT + CHECK в базата)."""

from enum import StrEnum


class AuditAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
