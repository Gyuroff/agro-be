"""Единственото място за „сега“. Никога datetime.now() директно (best-practices §C.9).

В тестовете времето се замразява с freezegun.
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

SOFIA = ZoneInfo("Europe/Sofia")


def now() -> datetime:
    """Текущият момент, UTC, с часова зона."""
    return datetime.now(UTC)


def today_sofia() -> date:
    """Бизнес „днес“ — по часова зона Europe/Sofia."""
    return now().astimezone(SOFIA).date()
