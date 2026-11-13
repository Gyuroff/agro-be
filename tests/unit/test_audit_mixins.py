"""Без база: mixin-ите дават правилните колони, relationship-и и оптимистично заключване."""

from decimal import Decimal
from enum import StrEnum

from sqlalchemy import BigInteger, Integer, Numeric
from sqlalchemy.orm import RelationshipProperty

from app.db.base import NAMING_CONVENTION, Base, Money, Pct, PricePerT, Tonnes, enum_check
from tests.audited_models import Event, Thing


def test_audit_mixin_columns() -> None:
    cols = Thing.__table__.c
    expected = {"created_at", "created_by_id", "updated_at", "updated_by_id", "version"}
    assert set(cols.keys()) >= expected
    assert isinstance(cols.created_by_id.type, BigInteger)
    assert isinstance(cols.version.type, Integer)
    assert not cols.created_by_id.nullable
    assert not cols.updated_by_id.nullable
    assert [fk.target_fullname for fk in cols.created_by_id.foreign_keys] == ["actors.id"]
    assert [fk.target_fullname for fk in cols.updated_by_id.foreign_keys] == ["actors.id"]


def test_audit_mixin_optimistic_locking() -> None:
    version_col = Thing.__mapper__.version_id_col
    assert version_col is not None
    assert version_col.key == "version"


def test_actor_relationships_lazy_raise() -> None:
    rels = Thing.__mapper__.relationships
    for key, fk in (("created_by", "created_by_id"), ("updated_by", "updated_by_id")):
        rel: RelationshipProperty[object] = rels[key]
        assert rel.lazy == "raise"
        assert rel.viewonly
        assert [c.key for c in rel.local_columns] == [fk]


def test_created_mixin_has_no_update_columns() -> None:
    cols = Event.__table__.c
    assert set(cols.keys()) == {"id", "created_at", "created_by_id"}
    assert Event.__mapper__.version_id_col is None
    assert set(Event.__mapper__.relationships.keys()) == {"created_by"}


def test_timestamptz() -> None:
    assert Thing.__table__.c.created_at.type.timezone is True  # pyright: ignore[reportAttributeAccessIssue]


def test_base_naming_convention() -> None:
    assert Base.metadata.naming_convention == NAMING_CONVENTION


def test_numeric_types() -> None:
    for type_, precision, scale in ((Money, 14, 2), (PricePerT, 12, 2), (Tonnes, 12, 3), (Pct, 5, 2)):
        assert isinstance(type_, Numeric)
        assert (type_.precision, type_.scale) == (precision, scale)
        assert type_.asdecimal
    assert Money.python_type is Decimal


class _Color(StrEnum):
    RED = "red"
    GREEN = "green"


def test_enum_check() -> None:
    ck = enum_check("color", _Color)
    assert str(ck.sqltext) == "color IN ('red','green')"
    assert ck.name == "color"
    assert enum_check("color", _Color, name="color_valid").name == "color_valid"
