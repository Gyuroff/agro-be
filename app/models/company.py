"""Фирма и номерация (docs/data-model.md §4)."""

from datetime import time
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Integer, Text, Time, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditMixin, Base, Money, Pct, Tonnes


class CompanySettings(AuditMixin, Base):
    """Точно един ред, `id = 1`."""

    __tablename__ = "company_settings"
    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    legal_name: Mapped[str] = mapped_column(Text, nullable=False)
    short_name: Mapped[str] = mapped_column(Text, nullable=False)
    eik: Mapped[str] = mapped_column(Text, nullable=False)
    vat_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    address_city: Mapped[str] = mapped_column(Text, nullable=False)
    address_line: Mapped[str] = mapped_column(Text, nullable=False)
    mol: Mapped[str] = mapped_column(Text, nullable=False)
    bank_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    iban: Mapped[str | None] = mapped_column(Text, nullable=True)
    bic: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_signature: Mapped[str] = mapped_column(Text, nullable=False)
    system_mailbox_address: Mapped[str] = mapped_column(Text, nullable=False)
    default_vat_rate: Mapped[Decimal] = mapped_column(Pct, nullable=False, server_default=text("20.00"))
    default_planned_truck_qty_t: Mapped[Decimal] = mapped_column(
        Tonnes, nullable=False, server_default=text("25.200")
    )
    invoice_rounding_tolerance_eur: Mapped[Decimal] = mapped_column(
        Money, nullable=False, server_default=text("0.01")
    )
    mi_invoice_grace_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("2"))
    mailbox_alarm_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("60"))
    email_undo_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("10"))
    app_lock_background_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("5")
    )
    biometric_max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("3"))
    shadow_cycles_required: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("3"))
    push_quiet_from: Mapped[time] = mapped_column(Time, nullable=False, server_default=text("'21:00'"))
    push_quiet_to: Mapped[time] = mapped_column(Time, nullable=False, server_default=text("'07:00'"))


class NumberSequence(AuditMixin, Base):
    """Naming Series: `invoice_main`, `invoice_pilot`, `email_ref`."""

    __tablename__ = "number_sequences"
    __table_args__ = (
        CheckConstraint(
            "range_start IS NULL OR range_end IS NULL OR next_number BETWEEN range_start AND range_end",
            name="next_in_range",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    next_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    range_start: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    range_end: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    width: Mapped[int] = mapped_column(Integer, nullable=False, default=10, server_default=text("10"))
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True, server_default=text("true"))
