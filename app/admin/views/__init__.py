"""Изгледите на уеб админа (docs/data-model.md §14). Предложенията — agro-msf.5."""

from app.admin.base import AuditedModelView
from app.admin.views.contracts import (
    ContractAdmin,
    ContractPriceAdmin,
    ExpenseAdmin,
    ExpenseCategoryAdmin,
    MonthlyResultOpeningAdmin,
)
from app.admin.views.identity import CompanyAdmin, DeviceAdmin, NumberSequenceAdmin, UserAdmin
from app.admin.views.masterdata import (
    ContactAdmin,
    CounterpartyAdmin,
    DomainAdmin,
    ProductAdmin,
    WarehouseAdmin,
)
from app.admin.views.readonly import (
    ActionCardAdmin,
    AgentRunAdmin,
    AuditLogAdmin,
    InboundEmailAdmin,
    InvoiceAdmin,
    InvoiceComparisonAdmin,
    MailboxPollAdmin,
    OutboundEmailAdmin,
    TripAdmin,
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
    ContractAdmin,
    ContractPriceAdmin,
    ExpenseCategoryAdmin,
    ExpenseAdmin,
    MonthlyResultOpeningAdmin,
    AuditLogAdmin,
    TripAdmin,
    InboundEmailAdmin,
    OutboundEmailAdmin,
    ActionCardAdmin,
    InvoiceAdmin,
    InvoiceComparisonAdmin,
    AgentRunAdmin,
    MailboxPollAdmin,
]
