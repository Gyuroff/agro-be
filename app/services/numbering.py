"""Номерация (docs/data-model.md §4, Naming Series). Номерът никога не се връща/преизползва."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.models.company import NumberSequence


class NumberRangeExhausted(DomainError):
    """Диапазонът е изчерпан (или номерацията е спряна след последния номер)."""

    def __init__(self, code: str) -> None:
        super().__init__(
            "number_range_exhausted",
            f"Номерацията „{code}“ е изчерпана или спряна.",
            http_status=409,
        )
        self.sequence_code = code


def take_next(session: Session, code: str) -> int:
    """Взема следващия номер със `SELECT … FOR UPDATE` в транзакцията на извикващия.

    Заключването се държи до commit/rollback, затова две паралелни сесии не получават един номер.
    Записът минава през одитния контекст на извикващия.

    `CHECK (next_number BETWEEN range_start AND range_end)` не допуска `range_end + 1`: след издаването
    на последния номер `next_number` остава `range_end`, а редът се спира (`is_active=false`).
    """
    seq = session.scalar(select(NumberSequence).where(NumberSequence.code == code).with_for_update())
    if seq is None:
        raise LookupError(f"Непозната номерация: {code!r}")
    if not seq.is_active:
        raise NumberRangeExhausted(code)
    number = seq.next_number
    if seq.range_end is not None and number >= seq.range_end:
        seq.is_active = False  # издаваме последния номер; следващото извикване гърми
    else:
        seq.next_number = number + 1
    session.flush()
    return number


def format_number(n: int, width: int) -> str:
    return str(n).zfill(width)
