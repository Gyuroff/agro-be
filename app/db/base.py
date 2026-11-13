"""Base, одитни mixin-и (docs/data-model.md §0, §0.1).

Одитните колони НЕ се попълват ръчно в кода: слушателят `before_flush` (задача agro-l2v.2) ги
взема от одитния контекст. Без контекст записът гърми с `MissingAuditContext`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column, relationship

if TYPE_CHECKING:
    from app.models.identity import Actor

# Стабилни имена на ограничения — иначе Alembic autogenerate е нестабилен.
# Всеки CheckConstraint ТРЯБВА да има име (`name="..."`), иначе конвенцията "ck" гърми.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Числени типове (data-model §0). В Python само Decimal (asdecimal=True по подразбиране).
Money = Numeric(14, 2)  # EUR
PricePerT = Numeric(12, 2)  # EUR/т
Tonnes = Numeric(12, 3)  # т
Pct = Numeric(5, 2)  # 20.00 = 20 %


def enum_check(col: str, enum: type[StrEnum], name: str | None = None) -> CheckConstraint:
    """`CHECK (col IN (...))` от StrEnum (data-model §0: TEXT + CHECK, без CREATE TYPE).

    Името по подразбиране е името на колоната → `ck_<table>_<col>` по конвенцията.
    """
    values = ",".join(f"'{member.value}'" for member in enum)
    return CheckConstraint(f"{col} IN ({values})", name=name or col)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    # TIMESTAMPTZ за всеки datetime (data-model §0: време в UTC)
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: DateTime(timezone=True)}


def _actor_relationship(owner: Any, fk_col: str) -> Mapped[Actor]:
    """`created_by`/`updated_by` към `actors`.

    lazy="raise": без скрити заявки — зареждай изрично (selectinload/joinedload).
    viewonly: id-тата се пишат само от before_flush (одитния контекст), не през relationship.
    """
    return relationship("Actor", foreign_keys=f"{owner.__name__}.{fk_col}", lazy="raise", viewonly=True)


class CreatedMixin:
    """Само за добавяне таблици (`audit_log`, `trip_events`, `mailbox_polls`, `invoice_trips`)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("actors.id"), nullable=False)

    @declared_attr
    def created_by(cls) -> Mapped[Actor]:
        return _actor_relationship(cls, "created_by_id")


class AuditMixin(CreatedMixin):
    """Одитни колони на всяка бизнес таблица + оптимистично заключване (`version`)."""

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_by_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("actors.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    @declared_attr
    def updated_by(cls) -> Mapped[Actor]:
        return _actor_relationship(cls, "updated_by_id")

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:
        # Оптимистично заключване: UPDATE ... WHERE version = :old → StaleDataError при конфликт
        # (API го превежда в 409 version_conflict).
        return {"version_id_col": cls.version}
