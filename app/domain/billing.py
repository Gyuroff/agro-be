"""График за фактуриране (docs/data-model.md §11, docs/api.md §6).

Датите са точно тези от RRULE: без преместване от почивни дни и празници (решение на собственика).
"""

from datetime import date, datetime, time, timedelta

from dateutil.rrule import rrulestr

_SEARCH_DAYS = 800

_WEEKDAYS_BG = {
    "MO": ("всеки", "понеделник"),
    "TU": ("всеки", "вторник"),
    "WE": ("всяка", "сряда"),
    "TH": ("всеки", "четвъртък"),
    "FR": ("всеки", "петък"),
    "SA": ("всяка", "събота"),
    "SU": ("всяка", "неделя"),
}


def billing_dates(rrule: str, dtstart: date, start: date, end: date) -> list[date]:
    """Датите за фактуриране в [start, end] (включително)."""
    if end < start:
        return []
    rule = rrulestr(rrule, dtstart=datetime.combine(dtstart, time.min))
    found = rule.between(datetime.combine(start, time.min), datetime.combine(end, time.max), inc=True)
    return [d.date() for d in found]


def next_billing_date(rrule: str, dtstart: date, today: date) -> date | None:
    """Първата дата за фактуриране `>= today` (днес още е предстояща); `None`, ако правилото свършва."""
    found = billing_dates(rrule, dtstart, today, today + timedelta(days=_SEARCH_DAYS))
    return found[0] if found else None


def previous_billing_date(rrule: str, dtstart: date, today: date) -> date | None:
    """Последната дата за фактуриране `< today`; `None`, ако няма такава след `dtstart`."""
    lower = max(dtstart, today - timedelta(days=_SEARCH_DAYS))
    found = billing_dates(rrule, dtstart, lower, today - timedelta(days=1))
    return found[-1] if found else None


def _parse_parts(rrule: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for item in rrule.split(";"):
        key, sep, value = item.partition("=")
        if not sep:
            raise ValueError(f"Невалиден RRULE: {rrule!r}")
        parts[key.strip().upper()] = value.strip().upper()
    return parts


def billing_label(mode: str, rrule: str | None) -> str:
    """Текстът за приложението (docs/api.md §6); `ValueError` за правило извън поддържаните."""
    if mode == "per_trip":
        return "на курс"
    if not rrule:
        raise ValueError("per_period изисква RRULE")
    parts = _parse_parts(rrule)
    freq = parts.get("FREQ")
    interval = int(parts.get("INTERVAL", "1"))
    if freq == "WEEKLY" and "BYDAY" in parts and interval == 1:
        days = [_WEEKDAYS_BG[d] for d in parts["BYDAY"].split(",")]
        out: list[str] = []
        prev_prefix = ""
        for prefix, name in days:
            out.append(name if prefix == prev_prefix else f"{prefix} {name}")
            prev_prefix = prefix
        return ", ".join(out[:-1]) + " и " + out[-1] if len(out) > 1 else out[0]
    if freq == "DAILY" and "BYDAY" not in parts and interval > 1:
        return f"на всеки {interval} дни"
    if freq == "MONTHLY" and "BYMONTHDAY" in parts and interval == 1:
        day = int(parts["BYMONTHDAY"])
        if day == -1:
            return "последен ден от месеца"
        if 1 <= day <= 31:
            return f"на {day}-о число"
    raise ValueError(f"Неподдържан RRULE за етикет: {rrule!r}")
