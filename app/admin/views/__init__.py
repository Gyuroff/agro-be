"""Изгледите на уеб админа (docs/data-model.md §14). Добавят се със задачите agro-wo5.2–wo5.4."""

from app.admin.base import AuditedModelView

ALL_VIEWS: list[type[AuditedModelView]] = []
