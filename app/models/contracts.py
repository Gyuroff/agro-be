"""Договори и цени (docs/data-model.md §6). Договорите са отделни по страна: продажба и покупка."""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AuditMixin, Base, PricePerT, Tonnes, enum_check

if TYPE_CHECKING:
    from app.models.masterdata import Contact, Counterparty, Product, Warehouse


class ContractSide(StrEnum):
    SALE = "sale"
    PURCHASE = "purchase"


class ContractStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


class PurchasePaymentMode(StrEnum):
    PREPAID = "prepaid"
    PAY_ON_INVOICE = "pay_on_invoice"
    PAY_AFTER_CLIENT = "pay_after_client"


class FundsHeldBy(StrEnum):
    COUNTERPARTY = "counterparty"  # наш аванс при тях
    US = "us"  # тяхна предплата при нас


class AdvanceBufferType(StrEnum):
    PERCENT = "percent"
    AMOUNT = "amount"


def _only_side(side: ContractSide, *columns: str) -> str:
    """`CHECK`: колоните са NULL, освен когато договорът е от дадената страна."""
    return f"side = '{side.value}' OR (" + " AND ".join(f"{c} IS NULL" for c in columns) + ")"


class Contract(AuditMixin, Base):
    __tablename__ = "contracts"
    __table_args__ = (
        enum_check("side", ContractSide),
        enum_check("status", ContractStatus),
        CheckConstraint(
            "billing_mode IS NULL OR billing_mode IN ('per_trip','per_period')", name="billing_mode"
        ),
        CheckConstraint(
            "purchase_payment_mode IS NULL OR purchase_payment_mode IN "
            "('prepaid','pay_on_invoice','pay_after_client')",
            name="purchase_payment_mode",
        ),
        CheckConstraint(
            "funds_held_by IS NULL OR funds_held_by IN ('counterparty','us')", name="funds_held_by"
        ),
        CheckConstraint(
            "advance_buffer_type IS NULL OR advance_buffer_type IN ('percent','amount')",
            name="advance_buffer_type",
        ),
        CheckConstraint("side <> 'purchase' OR warehouse_id IS NOT NULL", name="purchase_needs_warehouse"),
        CheckConstraint(_only_side(ContractSide.PURCHASE, "warehouse_id"), name="sale_no_warehouse"),
        CheckConstraint(_only_side(ContractSide.SALE, "supply_contract_id"), name="supply_only_sale"),
        CheckConstraint(
            _only_side(ContractSide.SALE, "billing_mode", "billing_rrule", "billing_dtstart"),
            name="billing_only_sale",
        ),
        CheckConstraint(
            _only_side(ContractSide.PURCHASE, "order_contact_id"), name="order_contact_only_purchase"
        ),
        CheckConstraint(
            _only_side(ContractSide.PURCHASE, "purchase_payment_mode"), name="payment_mode_only_purchase"
        ),
        CheckConstraint("side = 'sale' OR NOT tracks_customer_credit", name="credit_only_sale"),
        Index("ix_contracts_side_status", "side", "status"),
        Index("ix_contracts_counterparty_id_status", "counterparty_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    counterparty_id: Mapped[int] = mapped_column(ForeignKey("counterparties.id"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    warehouse_id: Mapped[int | None] = mapped_column(ForeignKey("warehouses.id"), nullable=True)
    supply_contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id"), nullable=True)
    order_contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"), nullable=True)
    contracted_qty_t: Mapped[Decimal | None] = mapped_column(Tonnes, nullable=True)
    opening_drawn_qty_t: Mapped[Decimal] = mapped_column(
        Tonnes, nullable=False, default=Decimal(0), server_default=text("0")
    )
    planned_truck_qty_t: Mapped[Decimal] = mapped_column(
        Tonnes, nullable=False, default=Decimal("25.200"), server_default=text("25.200")
    )
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    payment_terms_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    billing_mode: Mapped[str | None] = mapped_column(Text, nullable=True)
    billing_rrule: Mapped[str | None] = mapped_column(Text, nullable=True)
    billing_dtstart: Mapped[date | None] = mapped_column(Date, nullable=True)
    purchase_payment_mode: Mapped[str | None] = mapped_column(Text, nullable=True)
    tracks_customer_credit: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    funds_balance: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    funds_held_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    advance_buffer_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    advance_buffer_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default=ContractStatus.ACTIVE.value, server_default=ContractStatus.ACTIVE.value
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    counterparty: Mapped["Counterparty"] = relationship("Counterparty", foreign_keys=[counterparty_id])
    product: Mapped["Product"] = relationship("Product", foreign_keys=[product_id])
    warehouse: Mapped["Warehouse | None"] = relationship("Warehouse", foreign_keys=[warehouse_id])
    supply_contract: Mapped["Contract | None"] = relationship(
        "Contract", foreign_keys=[supply_contract_id], remote_side=[id]
    )
    order_contact: Mapped["Contact | None"] = relationship("Contact", foreign_keys=[order_contact_id])


class ContractPrice(AuditMixin, Base):
    """Цена по договор с история: нова цена = нов ред, старите не се пипат."""

    __tablename__ = "contract_prices"
    __table_args__ = (
        UniqueConstraint("contract_id", "packaging", "valid_from", name="contract_packaging_valid_from"),
        CheckConstraint("price_per_t > 0", name="price_positive"),
        CheckConstraint("packaging IN ('bulk','bagged')", name="packaging"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("contracts.id"), nullable=False)
    packaging: Mapped[str] = mapped_column(Text, nullable=False, default="bulk", server_default="bulk")
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    price_per_t: Mapped[Decimal] = mapped_column(PricePerT, nullable=False)

    contract: Mapped["Contract"] = relationship("Contract", foreign_keys=[contract_id])
