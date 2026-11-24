"""Обща база на изгледите (docs/data-model.md §14): едни и същи настройки за всеки модел."""

from typing import Any

from sqladmin import ModelView


class AuditedModelView(ModelView):
    """Без `model` — не се регистрира; наследниците го задават: `class X(AuditedModelView, model=Y)`."""

    form_excluded_columns = [
        "id",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "version",
    ]
    column_details_list = "__all__"  # включва одитните колони (кой/кога); наследниците я стесняват
    can_delete = False
    page_size = 50
    column_default_sort: Any = [("id", True)]


class ReadOnlyModelView(AuditedModelView):
    """Редове „само за четене“ (§14): без създаване, редакция и изтриване."""

    can_create = False
    can_edit = False
    can_delete = False
