"""Четене на договори за API: списък, детайл, търсене (docs/api.md §5–§6)."""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Literal, cast

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.errors import DomainError
from app.domain.billing import billing_label, next_billing_date
from app.domain.money import q2
from app.models.contracts import Contract, ContractPrice, ContractSide, ContractStatus, FundsHeldBy
from app.models.masterdata import Counterparty, Product, Warehouse
from app.models.trips import ClientRequestLine, Trip, TripStatus
from app.schemas.contracts import (
    ContractDetailDTO,
    ContractListDTO,
    ContractRowDTO,
    ContractSearchItemDTO,
    FundsDTO,
    PurchasePriceDTO,
    TripsSummaryDTO,
)
from app.services.contract_metrics import ContractMetrics, metrics, metrics_for
from app.services.trip_views import trip_list_items

FUNDS_LABELS = {
    FundsHeldBy.COUNTERPARTY.value: "Наш аванс при доставчика",
    FundsHeldBy.US.value: "Тяхна предплата при нас",
}
RECENT_TRIPS = 4
SEARCH_LIMIT = 20
PACKAGINGS = ("bulk", "bagged")


def _is_active(today: date) -> ColumnElement[bool]:
    return and_(
        Contract.status == ContractStatus.ACTIVE.value,
        or_(Contract.valid_to.is_(None), Contract.valid_to >= today),
    )


def get_contract(session: Session, contract_id: int) -> Contract:
    contract = session.get(Contract, contract_id)
    if contract is None:
        raise DomainError("not_found", "Договорът не е намерен.", http_status=404)
    return contract


def _names(
    session: Session, contracts: Sequence[Contract]
) -> tuple[dict[int, str], dict[int, str], dict[int, str]]:
    cps = dict(
        session.execute(
            select(Counterparty.id, Counterparty.name).where(
                Counterparty.id.in_({c.counterparty_id for c in contracts})
            )
        ).all()
    )
    products = dict(
        session.execute(
            select(Product.id, Product.name).where(Product.id.in_({c.product_id for c in contracts}))
        ).all()
    )
    wh = dict(
        session.execute(
            select(Warehouse.id, Warehouse.name).where(
                Warehouse.id.in_({c.warehouse_id for c in contracts if c.warehouse_id})
            )
        ).all()
    )
    return cps, products, wh


def _row(
    c: Contract, m: ContractMetrics, cps: dict[int, str], products: dict[int, str], wh: dict[int, str]
) -> ContractRowDTO:
    tracked = c.funds_balance is not None
    return ContractRowDTO(
        id=c.id,
        number=c.number,
        counterparty_name=cps[c.counterparty_id],
        warehouse_name=wh.get(c.warehouse_id) if c.warehouse_id else None,
        valid_to=c.valid_to,
        product=products[c.product_id],
        contracted_t=c.contracted_qty_t,
        drawn_t=m.drawn_t,
        requested_t=m.requested_t,
        remaining_t=m.remaining_t,
        state=m.state.value,  # type: ignore[arg-type]
        funds_balance=c.funds_balance if tracked else None,
        funds_held_by=cast("Literal['counterparty', 'us'] | None", c.funds_held_by if tracked else None),
        funds_label=FUNDS_LABELS.get(c.funds_held_by or "") if tracked else None,
    )


def list_contracts(session: Session, side: str | None, today: date | None = None) -> ContractListDTO:
    today = today or clock.today_sofia()
    active = _is_active(today)
    counts = dict(
        session.execute(select(Contract.side, func.count()).where(active).group_by(Contract.side)).all()  # type: ignore[arg-type]
    )
    stmt = select(Contract).where(active).order_by(Contract.number)
    if side is not None:
        stmt = stmt.where(Contract.side == side)
    contracts = list(session.scalars(stmt))
    ms = metrics_for(session, contracts)
    cps, products, wh = _names(session, contracts)
    sale = counts.get(ContractSide.SALE.value, 0)
    purchase = counts.get(ContractSide.PURCHASE.value, 0)
    return ContractListDTO(
        active_total=sale + purchase,
        sale_count=sale,
        purchase_count=purchase,
        items=[_row(c, ms[c.id], cps, products, wh) for c in contracts],
    )


def _current_prices(session: Session, contract_id: int, today: date) -> dict[str, Decimal]:
    """Последната цена към `today` по разфасовка."""
    rows = session.scalars(
        select(ContractPrice)
        .where(ContractPrice.contract_id == contract_id, ContractPrice.valid_from <= today)
        .order_by(ContractPrice.valid_from.desc())
    )
    out: dict[str, Decimal] = {}
    for row in rows:
        out.setdefault(row.packaging, row.price_per_t)
    return out


def _funds(session: Session, contract: Contract, today: date) -> FundsDTO | None:
    """Блокът `funds` (docs/api.md §6, agro-h9u). needed/buffer/shortfall — само за наш аванс."""
    if contract.funds_balance is None or contract.funds_held_by is None:
        return None
    balance = contract.funds_balance
    held_by = cast("Literal['counterparty', 'us']", contract.funds_held_by)
    label = FUNDS_LABELS[contract.funds_held_by]
    if held_by == FundsHeldBy.US.value:
        return FundsDTO(
            balance=balance, reserved=Decimal("0.00"), available=balance, held_by=held_by, label=label,
            needed=None, buffer=None, shortfall=None,
        )  # fmt: skip
    needed = _needed(session, contract.id, today)
    buffer = _buffer(contract, needed)
    return FundsDTO(
        balance=balance,
        reserved=needed,
        available=balance - needed,
        held_by=held_by,
        label=label,
        needed=needed,
        buffer=buffer,
        shortfall=max(Decimal("0.00"), needed + buffer - balance),
    )


def _needed(session: Session, contract_id: int, today: date) -> Decimal:
    """Σ(planned_qty_t × покупна цена по разфасовка) за planned курсове на договора."""
    prices = _current_prices(session, contract_id, today)
    default = next((prices[p] for p in PACKAGINGS if p in prices), Decimal("0"))
    rows = session.execute(
        select(Trip.planned_qty_t, ClientRequestLine.packaging)
        .outerjoin(ClientRequestLine, ClientRequestLine.trip_id == Trip.id)
        .where(Trip.purchase_contract_id == contract_id, Trip.status == TripStatus.PLANNED.value)
    ).all()
    total = sum((qty * prices.get(pack or "", default) for qty, pack in rows), Decimal("0"))
    return q2(total)


def _buffer(contract: Contract, needed: Decimal) -> Decimal:
    value = contract.advance_buffer_value
    if contract.advance_buffer_type is None or value is None:
        return Decimal("0.00")
    if contract.advance_buffer_type == "percent":
        return q2(needed * value / 100)
    return q2(value)


def contract_detail(session: Session, contract: Contract, today: date | None = None) -> ContractDetailDTO:
    today = today or clock.today_sofia()
    cps, products, wh = _names(session, [contract])
    row = _row(contract, metrics(session, contract), cps, products, wh)
    counterparty = session.get(Counterparty, contract.counterparty_id)
    assert counterparty is not None

    prices = _current_prices(session, contract.id, today)
    is_sale = contract.side == ContractSide.SALE.value
    purchase_prices = [
        PurchasePriceDTO(packaging=p, price_per_t=prices[p])  # type: ignore[arg-type]
        for p in PACKAGINGS
        if p in prices and not is_sale
    ]
    sale_price = None
    if is_sale and prices:
        # Най-новият ред независимо от разфасовката (като `price_at(packaging=None)`).
        sale_price = session.scalar(
            select(ContractPrice.price_per_t)
            .where(ContractPrice.contract_id == contract.id, ContractPrice.valid_from <= today)
            .order_by(ContractPrice.valid_from.desc())
            .limit(1)
        )

    supplier_name: str | None = None
    if not is_sale:
        supplier_name = counterparty.name
    elif contract.supply_contract_id is not None:
        supply = session.get(Contract, contract.supply_contract_id)
        supplier_name = cps_name(session, supply.counterparty_id) if supply else None

    label: str | None = None
    next_date: date | None = None
    mode = contract.billing_mode or counterparty.default_billing_mode
    rrule = contract.billing_rrule or counterparty.default_billing_rrule
    if is_sale and mode is not None:
        try:
            label = billing_label(mode, rrule)
        except ValueError:
            label = None
        if mode == "per_period" and rrule:
            next_date = next_billing_date(rrule, contract.billing_dtstart or contract.valid_from, today)

    count, qty = session.execute(
        select(func.count(), func.coalesce(func.sum(Trip.actual_qty_t), 0)).where(
            or_(Trip.sale_contract_id == contract.id, Trip.purchase_contract_id == contract.id),
            Trip.status == TripStatus.COMPLETED.value,
        )
    ).one()
    recent = session.scalars(
        select(Trip)
        .where(or_(Trip.sale_contract_id == contract.id, Trip.purchase_contract_id == contract.id))
        .order_by(Trip.planned_date.desc(), Trip.id.desc())
        .limit(RECENT_TRIPS)
    ).all()

    return ContractDetailDTO(
        **row.model_dump(),
        side=cast("Literal['sale', 'purchase']", contract.side),
        valid_from=contract.valid_from,
        sale_price_per_t=sale_price,
        purchase_price_per_t=purchase_prices[0].price_per_t if purchase_prices else None,
        purchase_prices=purchase_prices,
        purchase_counterparty_name=supplier_name,
        planned_truck_qty_t=contract.planned_truck_qty_t,
        billing_label=label,
        next_billing_date=next_date,
        payment_terms_days=contract.payment_terms_days or counterparty.default_payment_terms_days,
        purchase_payment_mode=cast(
            "Literal['prepaid', 'pay_on_invoice', 'pay_after_client'] | None",
            (contract.purchase_payment_mode or counterparty.default_purchase_payment_mode)
            if not is_sale
            else None,
        ),
        funds=_funds(session, contract, today),
        trips_summary=TripsSummaryDTO(count=count, qty_t=Decimal(str(qty)).quantize(Decimal("0.001"))),
        recent_trips=trip_list_items(session, list(recent), "short"),
    )


def cps_name(session: Session, counterparty_id: int) -> str | None:
    return session.scalar(select(Counterparty.name).where(Counterparty.id == counterparty_id))


def search_contracts(
    session: Session, q: str, side: str | None, active: bool, today: date | None = None
) -> list[ContractSearchItemDTO]:
    """Търсене по контрагент или номер (trigram `ILIKE`), до 20."""
    today = today or clock.today_sofia()
    pattern = f"%{q.strip().replace('%', r'\%').replace('_', r'\_')}%"
    stmt = (
        select(Contract)
        .join(Counterparty, Counterparty.id == Contract.counterparty_id)
        .where(or_(Counterparty.name.ilike(pattern), Contract.number.ilike(pattern)))
        .order_by(Contract.number)
        .limit(SEARCH_LIMIT)
    )
    if side is not None:
        stmt = stmt.where(Contract.side == side)
    if active:
        stmt = stmt.where(_is_active(today))
    contracts = list(session.scalars(stmt))
    ms = metrics_for(session, contracts)
    cps, products, _ = _names(session, contracts)
    return [
        ContractSearchItemDTO(
            id=c.id,
            number=c.number,
            counterparty_name=cps[c.counterparty_id],
            product=products[c.product_id],
            remaining_t=ms[c.id].remaining_t,
        )
        for c in contracts
    ]
