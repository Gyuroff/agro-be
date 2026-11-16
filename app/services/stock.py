"""Общ запас по покупните договори (agro-2lg, решение 4): карта `stock_low`.

Количествата на клиентите не се следят. Следи се сборът на `contracted_qty_t` на всички активни покупни
договори на стоката: когато остава по-малко от `STOCK_LOW_THRESHOLD_PCT` % от него, се отваря карта
(dedupe по стока). Договори без количество (`open`) не влизат в сбора. Картата се скрива ръчно.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core import clock
from app.models.cards import ActionCard, CardKind
from app.models.contracts import Contract, ContractSide, ContractStatus
from app.models.masterdata import Product
from app.schemas.cards import StockLowPayload
from app.services.cards import CardService
from app.services.contract_metrics import metrics_for

T3 = Decimal("0.001")
STOCK_LOW_THRESHOLD_PCT = Decimal("10")  # праг от разговора на 2026-11-16: остава <10% от сбора


def check_stock_low(session: Session, product_id: int, today: date | None = None) -> ActionCard | None:
    """Отваря (или връща вече отворената) карта, ако общият запас на стоката е под прага; иначе `None`."""
    today = today or clock.today_sofia()
    contracts = list(
        session.scalars(
            select(Contract).where(
                Contract.side == ContractSide.PURCHASE.value,
                Contract.status == ContractStatus.ACTIVE.value,
                Contract.product_id == product_id,
                Contract.valid_from <= today,
                or_(Contract.valid_to.is_(None), Contract.valid_to >= today),
                Contract.contracted_qty_t.is_not(None),
            )
        )
    )
    total = sum((c.contracted_qty_t or Decimal("0") for c in contracts), Decimal("0"))
    if total <= 0:
        return None
    per_contract = metrics_for(session, contracts)
    remaining = sum((m.remaining_t or Decimal("0") for m in per_contract.values()), Decimal("0"))
    pct = remaining * 100 / total
    if pct >= STOCK_LOW_THRESHOLD_PCT:
        return None
    product = session.get(Product, product_id)
    name = product.name if product is not None else "—"
    payload = StockLowPayload(
        product=name,
        contracts=len(contracts),
        total_t=total.quantize(T3),
        taken_t=(total - remaining).quantize(T3),
        remaining_t=remaining.quantize(T3),
        remaining_pct=pct.quantize(Decimal("0.01")),
    )
    card, _ = CardService.create_or_get(
        session,
        CardKind.STOCK_LOW,
        title=f"Запасът от {name} по договорите привършва",
        subtitle=f"остават {remaining:.3f} т ({payload.remaining_pct} %) от {total:.3f} т",
        payload=payload,
        dedupe_key=f"stock_low:{product_id}",
    )
    return card
