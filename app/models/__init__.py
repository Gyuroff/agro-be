"""Импортът на всички модели регистрира таблиците в Base.metadata (Alembic autogenerate)."""

from app.models.audit import AuditLog
from app.models.company import CompanySettings, NumberSequence
from app.models.contracts import Contract, ContractPrice
from app.models.identity import Actor, Device, RefreshToken, User
from app.models.masterdata import Contact, Counterparty, CounterpartyDomain, Product, Warehouse
from app.models.storage import StoredObject

__all__ = [
    "Actor",
    "AuditLog",
    "CompanySettings",
    "Contact",
    "Contract",
    "ContractPrice",
    "Counterparty",
    "CounterpartyDomain",
    "Device",
    "NumberSequence",
    "Product",
    "RefreshToken",
    "StoredObject",
    "User",
    "Warehouse",
]
