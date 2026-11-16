"""Четене на курсове за API: редове `TripListItemDTO` (docs/api.md §5)."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.errors import DomainError
from app.models.contracts import Contract
from app.models.masterdata import Counterparty, Product, Warehouse
from app.models.trips import Trip, TripEvent, TripStatus
from app.schemas.trips import (
    IdName,
    IdNumber,
    TripDetailDTO,
    TripEventDTO,
    TripGroupDTO,
    TripListDTO,
    TripListItemDTO,
)

RouteStyle = Literal["short", "long"]


def trip_list_items(
    session: Session, trips: Sequence[Trip], route_style: RouteStyle = "long"
) -> list[TripListItemDTO]:
    """`route_style=long` (табло): „Склад Пелет АД, Тервел → Агро Трейд ООД“.

    Курс без клиент (`unplanned`) няма „→ клиент“ — само доставчика. `short` още не е описан в
    спецификацията (виж bd note на agro-br9.1), затова засега е същото като `long`.
    """
    ids = {t.supplier_id for t in trips if t.supplier_id} | {t.customer_id for t in trips if t.customer_id}
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
        source = names[t.supplier_id] if t.supplier_id else "Доставчикът не е избран"
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


def list_trips(
    session: Session,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    status: str = "all",
    contract_id: int | None = None,
    today: date | None = None,
) -> TripListDTO:
    """Групи по `planned_date`; `actual` (реален тонаж на completed) за дни ≤ днес, иначе `planned`."""
    today = today or clock.today_sofia()
    stmt = select(Trip).order_by(Trip.planned_date, Trip.id)
    if date_from is not None:
        stmt = stmt.where(Trip.planned_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(Trip.planned_date <= date_to)
    if status != "all":
        stmt = stmt.where(Trip.status == status)
    if contract_id is not None:
        stmt = stmt.where((Trip.sale_contract_id == contract_id) | (Trip.purchase_contract_id == contract_id))
    trips = list(session.scalars(stmt))
    items = trip_list_items(session, trips, "short")
    by_day: dict[date, list[tuple[Trip, TripListItemDTO]]] = defaultdict(list)
    for trip, item in zip(trips, items, strict=True):
        by_day[trip.planned_date].append((trip, item))
    groups: list[TripGroupDTO] = []
    for day, rows in by_day.items():
        if day <= today:
            kind: Literal["actual", "planned"] = "actual"
            qty = sum(
                (
                    t.actual_qty_t
                    for t, _ in rows
                    if t.status == TripStatus.COMPLETED.value and t.actual_qty_t
                ),
                Decimal("0.000"),
            )
        else:
            kind = "planned"
            qty = sum(
                (t.planned_qty_t for t, _ in rows if t.status == TripStatus.PLANNED.value), Decimal("0.000")
            )
        groups.append(
            TripGroupDTO(date=day, count=len(rows), qty_t=qty, qty_kind=kind, items=[i for _, i in rows])
        )
    return TripListDTO(groups=groups)


def get_trip(session: Session, trip_id: int) -> Trip:
    trip = session.get(Trip, trip_id)
    if trip is None:
        raise DomainError("not_found", "Курсът не е намерен.", http_status=404)
    return trip


def check_version(trip: Trip, version: int) -> None:
    if trip.version != version:
        raise DomainError("version_conflict", "Курсът е променен от друг. Обновете и опитайте пак.", 409)


def allowed_actions(trip: Trip) -> list[Literal["cancel", "link_contract"]]:
    if trip.status == TripStatus.PLANNED.value:
        return ["cancel"]
    if trip.status == TripStatus.UNPLANNED.value:
        return ["link_contract"]
    return []


def trip_detail(session: Session, trip: Trip) -> TripDetailDTO:
    base = trip_list_items(session, [trip], "short")[0]
    supplier = session.get(Counterparty, trip.supplier_id)
    customer = session.get(Counterparty, trip.customer_id) if trip.customer_id else None
    sale = session.get(Contract, trip.sale_contract_id) if trip.sale_contract_id else None
    warehouse = session.get(Warehouse, trip.warehouse_id) if trip.warehouse_id else None
    assert supplier is not None
    events = session.scalars(
        select(TripEvent).where(TripEvent.trip_id == trip.id).order_by(TripEvent.occurred_at, TripEvent.id)
    )
    return TripDetailDTO(
        **base.model_dump(),
        customer=IdName(id=customer.id, name=customer.name) if customer else None,
        sale_contract=IdNumber(id=sale.id, number=sale.number) if sale else None,
        supplier=IdName(id=supplier.id, name=supplier.name),
        warehouse=warehouse.name if warehouse else None,
        driver_name=trip.driver_name,
        driver_phone=trip.driver_phone,
        load_date=trip.load_date,
        events=[TripEventDTO(at=e.occurred_at, text=e.text) for e in events],
        allowed_actions=allowed_actions(trip),
        version=trip.version,
    )
