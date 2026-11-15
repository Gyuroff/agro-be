"""Слушатели `before_flush` / `after_flush` и `audit_log` (docs/data-model.md §2.3).

- before_flush: попълва `created_by_id`/`updated_by_id`/`updated_at` от одитния контекст.
- after_flush: тук PK-тата вече съществуват и историята на атрибутите още е налична; пише редове
  в `audit_log` с Core `insert` през връзката на сесията (`session.add` в after_flush е забранен).

Това е ЕДИНСТВЕНОТО място в `app/`, където е позволен sqlalchemy `insert(...)` върху одита;
`update(...)`/`delete(...)` върху модели са забранени навсякъде (tests/test_no_bulk_dml.py).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import event, func, insert, inspect
from sqlalchemy.orm import Session
from sqlalchemy.orm.base import NO_VALUE
from sqlalchemy.orm.unitofwork import UOWTransaction

from app.core.audit_context import AuditContext, AuditSource, get_audit_context
from app.db.base import AuditMixin, CreatedMixin
from app.models.enums import AuditAction

# Имат одитни колони, но не и ред за промяна в audit_log (§2.3 т.3).
AUDIT_EXCLUDED_TABLES: frozenset[str] = frozenset(
    {"audit_log", "mailbox_polls", "agent_runs", "push_notifications"}
)
# Колони, чиято промяна сама по себе не се одитира.
AUDIT_EXCLUDED_COLUMNS: Mapping[str, frozenset[str]] = {"refresh_tokens": frozenset({"last_used_at"})}
# Служебни колони — не влизат в `changes`.
AUDIT_SKIPPED_COLUMNS: frozenset[str] = frozenset(
    {"updated_at", "updated_by_id", "version", "created_at", "created_by_id"}
)
MASK = "***"
_SENSITIVE_COLUMNS: frozenset[str] = frozenset(
    {"password_hash", "refresh_token_hash", "token_hash", "push_token", "smtp_password"}
)
# Само в тези източници изричен created_by_id/updated_by_id не се презаписва от контекста.
_OVERRIDE_SOURCES = frozenset({AuditSource.SEED, AuditSource.MIGRATION})


def is_sensitive(column: str) -> bool:
    return column in _SENSITIVE_COLUMNS or column.endswith(("_secret", "_token"))


def serialize(value: Any) -> Any:
    """JSON-безопасна стойност: Decimal → str (без загуба на нули), date/datetime → ISO, enum → value."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Enum):
        return serialize(value.value)
    if isinstance(value, Decimal | UUID):
        return str(value)
    if isinstance(value, date):  # включва datetime
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): serialize(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, list | tuple | set | frozenset):
        return [serialize(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    return str(value)


def _cell(column: str, value: Any) -> Any:
    if value is not None and is_sensitive(column):
        return MASK
    return serialize(value)


def _audited(obj: object) -> bool:
    if not isinstance(obj, CreatedMixin):
        return False
    return inspect(obj).mapper.local_table.name not in AUDIT_EXCLUDED_TABLES


def _changes(obj: object, action: AuditAction) -> dict[str, list[Any]]:
    state = inspect(obj)
    table = state.mapper.local_table
    excluded = AUDIT_EXCLUDED_COLUMNS.get(table.name, frozenset())
    out: dict[str, list[Any]] = {}
    for prop in state.mapper.column_attrs:
        column = prop.columns[0]
        name = column.name
        if name in AUDIT_SKIPPED_COLUMNS or name in excluded:
            continue
        if action is AuditAction.UPDATE:
            history = state.attrs[prop.key].history
            if not history.has_changes():
                continue
            old = history.deleted[0] if history.deleted else None
            new = history.added[0] if history.added else None
            if old is NO_VALUE:
                old = None
            if old == new:
                continue
            out[name] = [_cell(name, old), _cell(name, new)]
            continue
        value = state.dict.get(prop.key, NO_VALUE)
        if value is NO_VALUE:
            if column.server_default is not None or column.primary_key:
                continue  # стойността е на сървъра и не е заредена; не я четем наново
            value = None
        if action is AuditAction.CREATE:
            out[name] = [None, _cell(name, value)]
        else:
            out[name] = [_cell(name, value), None]
    return out


def _insert_rows(session: Session, rows: list[dict[str, Any]]) -> None:
    # Локален импорт: app.models.audit ражда app.db.base → app.db → този модул (кръг при импорт).
    from app.models.audit import AuditLog

    session.connection().execute(insert(AuditLog), rows)


def _entity_id(obj: object) -> int:
    key = inspect(obj).mapper.primary_key_from_instance(obj)
    return int(key[0])


def _row(
    ctx: AuditContext, action: AuditAction, entity_type: str, entity_id: int, changes: Any
) -> dict[str, Any]:
    return {
        "actor_id": ctx.actor_id,
        "source": ctx.source.value,
        "request_id": ctx.request_id,
        "reason": ctx.reason,
        "action": action.value,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "changes": changes,
    }


def _before_flush(session: Session, flush_context: UOWTransaction, instances: object | None) -> None:
    new = [o for o in session.new if isinstance(o, CreatedMixin)]
    dirty = [
        o
        for o in session.dirty
        if isinstance(o, AuditMixin) and session.is_modified(o, include_collections=False)
    ]
    deleted = [o for o in session.deleted if _audited(o)]
    if not (new or dirty or deleted):
        return
    ctx = get_audit_context()  # MissingAuditContext, ако няма
    may_override = ctx.source in _OVERRIDE_SOURCES
    for obj in new:
        if obj.created_by_id is None or not may_override:
            obj.created_by_id = ctx.actor_id
        if isinstance(obj, AuditMixin) and (obj.updated_by_id is None or not may_override):
            obj.updated_by_id = ctx.actor_id
    for obj in dirty:
        assert isinstance(obj, AuditMixin)
        obj.updated_by_id = ctx.actor_id
        obj.updated_at = func.now()  # pyright: ignore[reportAttributeAccessIssue]


def _after_flush(session: Session, flush_context: UOWTransaction) -> None:
    rows: list[dict[str, Any]] = []
    ctx: AuditContext | None = None
    batches: Sequence[tuple[Iterable[object], AuditAction]] = (
        (session.new, AuditAction.CREATE),
        (session.dirty, AuditAction.UPDATE),
        (session.deleted, AuditAction.DELETE),
    )
    for objects, action in batches:
        for obj in list(objects):
            if not _audited(obj):
                continue
            changes = _changes(obj, action)
            if action is AuditAction.UPDATE and not changes:
                continue
            ctx = ctx or get_audit_context()
            rows.append(_row(ctx, action, inspect(obj).mapper.local_table.name, _entity_id(obj), changes))
    if rows:
        _insert_rows(session, rows)


def record_bulk(
    session: Session, entity_type: str, ids: Iterable[int], changes: Mapping[str, tuple[Any, Any]]
) -> None:
    """Изключение за масови промени, направени извън ORM (в същата транзакция).

    `changes`: `{"колона": (old, new)}` — един и същ запис за всеки id. Маскира чувствителните колони.
    """
    ctx = get_audit_context()
    payload = {col: [_cell(col, old), _cell(col, new)] for col, (old, new) in changes.items()}
    rows = [_row(ctx, AuditAction.UPDATE, entity_type, entity_id, payload) for entity_id in ids]
    if rows:
        _insert_rows(session, rows)


def register_listeners() -> None:
    """Идемпотентно; вика се при импорт на `app.db`."""
    if not event.contains(Session, "before_flush", _before_flush):
        event.listen(Session, "before_flush", _before_flush)
    if not event.contains(Session, "after_flush", _after_flush):
        event.listen(Session, "after_flush", _after_flush)


register_listeners()
