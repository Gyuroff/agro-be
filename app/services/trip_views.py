"""Четене на курсове за API: редове `TripListItemDTO` (docs/api.md §5)."""

from collections.abc import Sequence
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.masterdata import Counterparty, Product, Warehouse
from app.models.trips import Trip
from app.schemas.trips import TripListItemDTO

RouteStyle = Literal["short", "long"]


def trip_list_items(
    session: Session, trips: Sequence[Trip], route_style: RouteStyle = "long"
) -> list[TripListItemDTO]:
    """`route_style=long` (табло): „Склад Пелет АД, Тервел → Агро Трейд ООД“.

    Курс без клиент (`unplanned`) няма „→ клиент“ — само доставчика. `short` още не е описан в
    спецификацията (виж bd note на agro-br9.1), затова засега е същото като `long`.
    """
    ids = {t.supplier_id for t in trips} | {t.customer_id for t in trips if t.customer_id}
    names = dict(
        session.execute(select(Counterparty.id, Counterparty.name).where(Counterparty.id.in_(ids))).all()
    )
    products = dict(
        session.execute(
            select(Product.id, Product.name).where(Product.id.in_({t.product_id for t in trips}))
        ).all()
    )
    wh_ids = {t.warehouse_id for t in trips if t.warehouse_id}
    warehouses = dict(
        session.execute(select(Warehouse.id, Warehouse.name).where(Warehouse.id.in_(wh_ids))).all()
    )
    items: list[TripListItemDTO] = []
    for t in trips:
        source = names[t.supplier_id]
        if route_style == "long":
            source = f"Склад {source}" + (f", {warehouses[t.warehouse_id]}" if t.warehouse_id else "")
        route = f"{source} → {names[t.customer_id]}" if t.customer_id else source
        items.append(
            TripListItemDTO(
                id=t.id,
                plate=t.plate_display,
                product=products[t.product_id],
                route=route,
                date=t.planned_date,
                status=t.status,
                actual_qty_t=t.actual_qty_t,
                planned_qty_t=t.planned_qty_t,
            )
        )
    return items
