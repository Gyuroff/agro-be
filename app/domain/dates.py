"""Работни дни по българския календар: събота, неделя, официални празници и компенсираните почивни дни."""

from datetime import date, timedelta
from functools import cache

import holidays


@cache
def _bg_holidays(year: int) -> frozenset[date]:
    return frozenset(holidays.country_holidays("BG", years=year))


def is_working_day(d: date) -> bool:
    return d.weekday() < 5 and d not in _bg_holidays(d.year)


def previous_working_day(d: date) -> date:
    """`d`, ако е работен ден, иначе най-близкият предишен работен ден (колкото и дни назад да е)."""
    while not is_working_day(d):
        d -= timedelta(days=1)
    return d


def add_working_days(d: date, n: int) -> date:
    """Отмества с `n` работни дни (`n` може да е отрицателно); `n=0` връща `d` непроменена."""
    step = 1 if n >= 0 else -1
    remaining = abs(n)
    while remaining:
        d += timedelta(days=step)
        if is_working_day(d):
            remaining -= 1
    return d
