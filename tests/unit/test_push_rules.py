"""Правила за пуш: шаблони, тихи часове (docs/push.md §1–§2)."""

from datetime import UTC, datetime, time
from decimal import Decimal

import pytest

from app.models.cards import ActionCard, CardKind
from app.push import rules

QF, QT = time(21, 0), time(7, 0)


def at(hour: int, minute: int = 0, day: int = 16) -> datetime:
    """Час по Europe/Sofia (ноември = UTC+2)."""
    return datetime(2026, 11, day, hour - 2, minute, tzinfo=UTC)


@pytest.mark.parametrize(
    ("moment", "quiet"),
    [(at(10), False), (at(20, 59), False), (at(21), True), (at(23), True), (at(3), True), (at(7), False)],
)
def test_in_quiet_hours(moment: datetime, quiet: bool) -> None:
    assert rules.in_quiet_hours(moment, QF, QT) is quiet


def test_schedule_defers_non_urgent_to_end_of_quiet_but_not_urgent() -> None:
    assert rules.schedule_for(at(10), QF, QT, urgent=False) == at(10)
    assert rules.schedule_for(at(23), QF, QT, urgent=False) == at(7, day=17)
    assert rules.schedule_for(at(3), QF, QT, urgent=False) == at(7)
    assert rules.schedule_for(at(23), QF, QT, urgent=True) == at(23)


def test_money_and_tonnes_formats() -> None:
    assert rules.fmt_money(Decimal("32851.2")) == "32 851,20"
    assert rules.fmt_t("66.000") == "66,00"


def test_render_cancellation() -> None:
    card = ActionCard(
        kind=CardKind.CANCELLATION.value,
        title="x",
        payload={
            "trip_ids": [1, 2],
            "date": "2026-11-13",
            "plates": ["СВ 7890 ТР", "РВ 1122 АВ"],
            "supplier_name": "Пелет АД",
            "notify_supplier": True,
        },
    )
    fields = rules.card_fields(card, counterparty_name="Агро Трейд", suppliers=[])
    title, body = rules.render(card.kind, fields)
    assert title == "Отказани 2 курса от Агро Трейд"
    assert body == "13.11 · СВ 7890 ТР, РВ 1122 АВ. Потвърди отказа."
    assert rules.is_urgent(card.kind)


def test_urgent_set_matches_spec() -> None:
    urgent = {k for k in rules.TEMPLATES if rules.is_urgent(k)}
    assert urgent == {"cancellation", "send_failed", "storage_upload_failed", "supplier_advance_low"}
