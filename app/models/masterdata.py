"""Мастър данни: контрагенти, домейни, контакти, стоки, складове (docs/data-model.md §5)."""

from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.db.base import AuditMixin, Base, Pct, enum_check
from app.domain.emails import is_public_domain, normalize_domain, normalize_email


class InvoicingMode(StrEnum):
    SHADOW = "shadow"
    ERP = "erp"


class BillingMode(StrEnum):
    PER_TRIP = "per_trip"
    PER_PERIOD = "per_period"


class ContactSource(StrEnum):
    MANUAL = "manual"
    LEARNED = "learned"  # „Запомни адреса“


class ProductUnit(StrEnum):
    TONNE = "t"


class OrderFormat(StrEnum):
    """Формат на таблицата в писмото към доставчика (agro-4hs): настройка на контрагента."""

    PER_TRUCK = "per_truck"  # Дата | Камион | Шофьор | К-во
    PER_DAY = "per_day"  # Дата на експедиция | Вид на автомобила/товара | Количество | Общо
    PER_PACKAGING_COUNT = "per_packaging_count"  # брой курсове по вид опаковка
    FREE_SCHEDULE = "free_schedule"  # свободен график по дни (текст)
    XLSX_TEMPLATE = "xlsx_template"  # кратко писмо + xlsx по шаблон на доставчика


class Counterparty(AuditMixin, Base):
    """Клиент и/или доставчик; ролята е по договор."""

    __tablename__ = "counterparties"
    __table_args__ = (
        CheckConstraint("eik ~ '^[0-9]{9}([0-9]{4})?$'", name="eik_format"),
        CheckConstraint("invoicing_mode <> 'erp' OR microinvest_code IS NOT NULL", name="erp_needs_mi_code"),
        enum_check("invoicing_mode", InvoicingMode),
        enum_check("order_format", OrderFormat),
        CheckConstraint(
            "default_billing_mode IS NULL OR default_billing_mode IN ('per_trip','per_period')",
            name="default_billing_mode",
        ),
        CheckConstraint(
            "default_purchase_payment_mode IS NULL OR default_purchase_payment_mode IN "
            "('prepaid','pay_on_invoice','pay_after_client')",
            name="default_purchase_payment_mode",
        ),
        Index(
            "ix_counterparties_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    legal_name: Mapped[str] = mapped_column(Text, nullable=False)
    eik: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    vat_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_vat_registered: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    country_code: Mapped[str] = mapped_column(
        String(2), nullable=False, default="BG", server_default=text("'BG'")
    )
    address_city: Mapped[str | None] = mapped_column(Text, nullable=True)
    address_line: Mapped[str | None] = mapped_column(Text, nullable=True)
    mol: Mapped[str | None] = mapped_column(Text, nullable=True)
    microinvest_code: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    invoice_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    invoicing_mode: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=InvoicingMode.SHADOW.value,
        server_default=InvoicingMode.SHADOW.value,
    )
    merge_contracts_in_invoice: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    default_billing_mode: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_billing_rrule: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_payment_terms_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    default_purchase_payment_mode: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_format: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=OrderFormat.PER_TRUCK.value,
        server_default=OrderFormat.PER_TRUCK.value,
    )
    # Адаптер към файла на клиента (app/adapters/client_files; agro-cmf): „ameta“ за Пилко/Амета. NULL = не
    # връщаме файл, само писмото. Всеки клиент има свои правила за файла, затова е адаптер, не колона.
    client_file_adapter: Mapped[str | None] = mapped_column(Text, nullable=True)
    truck_type: Mapped[str | None] = mapped_column(Text, nullable=True)  # клиент: „фуражовоз“ (свои камиони)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


class CounterpartyDomain(AuditMixin, Base):
    __tablename__ = "counterparty_domains"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    counterparty_id: Mapped[int] = mapped_column(ForeignKey("counterparties.id"), nullable=False)
    domain: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    @validates("domain")
    def _validate_domain(self, _key: str, value: str) -> str:
        domain = normalize_domain(value)
        if is_public_domain(domain):
            raise ValueError(f"публичен домейн не може да идентифицира контрагент: {domain}")
        return domain


class Contact(AuditMixin, Base):
    __tablename__ = "contacts"
    __table_args__ = (
        CheckConstraint("email IS NOT NULL OR phone IS NOT NULL", name="email_or_phone"),
        enum_check("source", ContactSource),
        Index(
            "uq_contacts_email_active",
            "email",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    counterparty_id: Mapped[int] = mapped_column(ForeignKey("counterparties.id"), nullable=False)
    full_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    sends_requests: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    sends_reports: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    receives_orders: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    receives_invoices: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    source: Mapped[str] = mapped_column(
        Text, nullable=False, default=ContactSource.MANUAL.value, server_default=ContactSource.MANUAL.value
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    @validates("email")
    def _normalize_email(self, _key: str, value: str | None) -> str | None:
        return None if value is None else normalize_email(value)


class Product(AuditMixin, Base):
    __tablename__ = "products"
    __table_args__ = (enum_check("unit", ProductUnit),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    unit: Mapped[str] = mapped_column(
        Text, nullable=False, default=ProductUnit.TONNE.value, server_default=ProductUnit.TONNE.value
    )
    microinvest_code: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    vat_rate: Mapped[Decimal] = mapped_column(
        Pct, nullable=False, default=Decimal("20.00"), server_default=text("20.00")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


class Warehouse(AuditMixin, Base):
    """Склад на доставчик — място на товарене."""

    __tablename__ = "warehouses"
    __table_args__ = (
        UniqueConstraint("counterparty_id", "name", name="counterparty_name"),
        CheckConstraint(
            "cardinality(packagings) > 0 AND packagings <@ ARRAY['bulk','bagged']::text[]",
            name="packagings",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    counterparty_id: Mapped[int] = mapped_column(ForeignKey("counterparties.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    packagings: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        default=lambda: ["bulk", "bagged"],
        server_default=text("'{bulk,bagged}'"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
