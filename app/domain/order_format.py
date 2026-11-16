"""Таблицата в писмото до доставчика по формат на контрагента (agro-4hs). Чисти функции, без база."""

from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from app.domain.plates import format_plate

PACKAGING_LABEL = {"bulk": "насипни", "bagged": "опаковани"}


# Редът е ORM обект (`ClientRequestLine`) или негов двойник в тест: ползват се `planned_date`, `plate_raw`,
# `driver_name`, `planned_qty_t`, `packaging`, `own_transport`. `Any`, защото pyright не мери Mapped[...]
# срещу Protocol.
OrderLine = Any


def fmt_qty(value: Decimal) -> str:
    return f"{value:.3f}"


OWN_TRANSPORT_TRUCK = "гондола"  # „транспорт Ива“: наш камион


def effective_truck_type(line: OrderLine, client_default: str | None) -> str | None:
    """Ръчният тип на реда → „гондола“ при наш транспорт → типът по подразбиране на клиента."""
    return line.truck_type or (OWN_TRANSPORT_TRUCK if line.own_transport else client_default)


def _kind(packaging: str | None) -> str:
    return PACKAGING_LABEL.get(packaging or "", "—")


def _total(lines: Sequence[OrderLine]) -> Decimal:
    return sum((line.planned_qty_t for line in lines), Decimal("0"))


def _per_truck(lines: Sequence[OrderLine], truck: str | None = None) -> list[str]:
    rows = ["Дата | Камион | Шофьор | К-во (т)"]
    for line in lines:
        plate = "транспорт Ива" if line.own_transport else format_plate(line.plate_raw)
        if (kind := effective_truck_type(line, truck)) and kind != truck:
            plate = f"{plate} ({kind})"
        rows.append(
            f"{line.planned_date.strftime('%d.%m.%Y')} | {plate} | "
            f"{line.driver_name or '—'} | {fmt_qty(line.planned_qty_t)}"
        )
    return rows


def _per_day(lines: Sequence[OrderLine], truck: str | None = None) -> list[str]:
    groups: dict[tuple[date, str], list[OrderLine]] = defaultdict(list)
    for line in lines:
        kind = ", ".join(
            p for p in (effective_truck_type(line, truck), _kind(line.packaging)) if p and p != "—"
        )
        groups[(line.planned_date, kind or "—")].append(line)
    rows = ["Дата на експедиция | Вид | Камиони | Количество (т)"]
    for (day, kind), items in sorted(groups.items()):
        rows.append(f"{day.strftime('%d.%m.%Y')} | {kind} | {len(items)} | {fmt_qty(_total(items))}")
    return rows


def _per_packaging_count(lines: Sequence[OrderLine], truck: str | None = None) -> list[str]:
    counts = Counter(_kind(line.packaging) for line in lines)
    rows = ["Опаковка | Курсове | Количество (т)"]
    for kind in sorted(counts):
        items = [line for line in lines if _kind(line.packaging) == kind]
        rows.append(f"{kind} | {counts[kind]} | {fmt_qty(_total(items))}")
    return rows


def _free_schedule(lines: Sequence[OrderLine], truck: str | None = None) -> list[str]:
    per_day: dict[date, list[OrderLine]] = defaultdict(list)
    for line in lines:
        per_day[line.planned_date].append(line)
    return [
        f"{day.strftime('%d.%m')} — {len(items)} камиона, {fmt_qty(_total(items))} т"
        for day, items in sorted(per_day.items())
    ]


_RENDERERS = {
    "per_truck": _per_truck,
    "per_day": _per_day,
    "per_packaging_count": _per_packaging_count,
    "free_schedule": _free_schedule,
    "xlsx_template": _per_day,  # xlsx по шаблон още не се генерира — засега таблица в тялото
}


def render_table(order_format: str, lines: Sequence[OrderLine], truck: str | None = None) -> str:
    """`truck` — типът камион по подразбиране на клиента (`counterparties.truck_type`)."""
    return "\n".join(_RENDERERS.get(order_format, _per_truck)(lines, truck))


def render_body(
    order_format: str, greeting: str, lines: Sequence[OrderLine], truck: str | None = None
) -> str:
    """Поздрав + таблица + общо; подписът го добавя `smtp.render_body`."""
    return (
        f"{greeting.rstrip()}\n\n{render_table(order_format, lines, truck)}"
        f"\n\nОбщо: {len(lines)} камиона, {fmt_qty(_total(lines))} т"
    )
