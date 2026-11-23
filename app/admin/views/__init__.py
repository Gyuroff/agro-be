"""Изгледите на уеб админа (docs/data-model.md §14). Добавят се със задачите agro-wo5.2–wo5.4."""

from app.admin.base import AuditedModelView
from app.admin.views.identity import CompanyAdmin, DeviceAdmin, NumberSequenceAdmin, UserAdmin
from app.admin.views.masterdata import (
    ContactAdmin,
    CounterpartyAdmin,
    DomainAdmin,
    ProductAdmin,
    WarehouseAdmin,
)

ALL_VIEWS: list[type[AuditedModelView]] = [
    UserAdmin,
    DeviceAdmin,
    CompanyAdmin,
    NumberSequenceAdmin,
    CounterpartyAdmin,
    DomainAdmin,
    ContactAdmin,
    ProductAdmin,
    WarehouseAdmin,
]
