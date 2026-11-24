"""Търсене на контрагенти за „Свържи“ (docs/api.md §5, sheet 7): по име, до 20, предложеният е първи."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.cards import ActionCard, CardKind
from app.models.contracts import Contract, ContractSide, ContractStatus
from app.models.masterdata import Counterparty
from app.schemas.counterparties import CounterpartySearchItemDTO

SEARCH_LIMIT = 20


def _plural(n: int) -> str:
    return f"{n} договор" if n == 1 else f"{n} договора"


def role_label(sale: int, purchase: int) -> str:
    if sale and purchase:
        return "Клиент и доставчик"
    if sale:
        return f"Клиент · {_plural(sale)}"
    if purchase:
        return f"Доставчик · {_plural(purchase)}"
    return "Без активни договори"


def _suggested_id(session: Session, card_id: int | None) -> int | None:
    if card_id is None:
        return None
    card = session.get(ActionCard, card_id)
    if card is None or card.kind != CardKind.UNKNOWN_SENDER.value:
        return None
    value = card.payload.get("suggested_counterparty_id")
    return value if isinstance(value, int) else None


def search_counterparties(
    session: Session, q: str, card_id: int | None = None
) -> list[CounterpartySearchItemDTO]:
    pattern = f"%{q.strip().replace('\\', '\\\\').replace('%', r'\%').replace('_', r'\_')}%"
    suggested_id = _suggested_id(session, card_id)
    found = list(
        session.scalars(
            select(Counterparty)
            .where(Counterparty.name.ilike(pattern))
            .order_by((Counterparty.id == suggested_id).desc(), Counterparty.name, Counterparty.id)
            .limit(SEARCH_LIMIT)
        )
    )
    counts: dict[tuple[int, str], int] = {
        (cp_id, side): n
        for cp_id, side, n in session.execute(
            select(Contract.counterparty_id, Contract.side, func.count())
            .where(
                Contract.counterparty_id.in_([c.id for c in found]),
                Contract.status == ContractStatus.ACTIVE.value,
            )
            .group_by(Contract.counterparty_id, Contract.side)
        )
    }
    return [
        CounterpartySearchItemDTO(
            id=c.id,
            name=c.name,
            role_label=role_label(
                counts.get((c.id, ContractSide.SALE.value), 0),
                counts.get((c.id, ContractSide.PURCHASE.value), 0),
            ),
            suggested=c.id == suggested_id,
        )
        for c in found
    ]
