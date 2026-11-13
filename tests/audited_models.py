"""Тестови модели с одитните mixin-и в отделна metadata (не замърсяват Base.metadata / Alembic).

`Actor` тук сочи същата таблица `actors`, за да се разрешат `created_by`/`updated_by` в този регистър.
"""

from sqlalchemy import BigInteger, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.base import NAMING_CONVENTION, AuditMixin, CreatedMixin


class AuditedTestBase(DeclarativeBase):
    pass


AuditedTestBase.metadata.naming_convention = NAMING_CONVENTION


class Actor(AuditedTestBase):
    __tablename__ = "actors"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(Text)


class Thing(AuditedTestBase, AuditMixin):
    __tablename__ = "test_things"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text)


class Event(AuditedTestBase, CreatedMixin):
    __tablename__ = "test_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
