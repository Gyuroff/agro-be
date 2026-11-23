"""Импортът на всички модели регистрира таблиците в Base.metadata (Alembic autogenerate)."""

from app.models.audit import AuditLog
from app.models.cards import ActionCard, AgentRun, PushNotification
from app.models.company import CompanySettings, NumberSequence
from app.models.contracts import Contract, ContractPrice
from app.models.identity import Actor, Device, RefreshToken, User
from app.models.mail import EmailAttachment, InboundEmail, MailboxPoll, OutboundEmail
from app.models.masterdata import Contact, Counterparty, CounterpartyDomain, Product, Warehouse
from app.models.proposals import ActionProposal
from app.models.reports import SupplierReport, SupplierReportLine
from app.models.result import Expense, ExpenseCategory, MonthlyResultOpening
from app.models.storage import StoredObject
from app.models.trips import ClientRequest, ClientRequestLine, Trip, TripEvent

__all__ = [
    "ActionCard",
    "ActionProposal",
    "Actor",
    "AgentRun",
    "AuditLog",
    "ClientRequest",
    "ClientRequestLine",
    "CompanySettings",
    "Contact",
    "Contract",
    "ContractPrice",
    "Counterparty",
    "CounterpartyDomain",
    "Device",
    "EmailAttachment",
    "Expense",
    "ExpenseCategory",
    "InboundEmail",
    "MailboxPoll",
    "MonthlyResultOpening",
    "NumberSequence",
    "OutboundEmail",
    "Product",
    "PushNotification",
    "RefreshToken",
    "StoredObject",
    "SupplierReport",
    "SupplierReportLine",
    "Trip",
    "TripEvent",
    "User",
    "Warehouse",
]
