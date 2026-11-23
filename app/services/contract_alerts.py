"""Карта `contract_over` (docs/api.md §1, data-model.md §6): „Договорът с X не стига“.

`check_over` се вика след всяка промяна на изтеглено/заявено: създаване и отказ на курсове, потвърждаване
на справка. `remaining < 0` → една отворена карта (`dedupe_key=contract_over:{id}`, числата се опресняват);
иначе отворената карта става `superseded`.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.models.cards import ActionCard, CardKind, CardStatus
from app.models.contracts import Contract
from app.models.masterdata import Counterparty, Product
from app.schemas.cards import ContractOverPayload
from app.services.cards import CardService
from app.services.contract_metrics import ContractState, metrics

T3 = Decimal("0.001")


def check_over(session: Session, contract: Contract) -> ActionCard | None:
    """Отворената карта, ако договорът не стига; `None` (и supersede на старата карта), ако стига."""
    m = metrics(session, contract)
    key = f"contract_over:{contract.id}"
    if m.state is not ContractState.OVER or m.remaining_t is None:
        existing = session.scalar(
            select(ActionCard).where(ActionCard.dedupe_key == key, ActionCard.status == CardStatus.OPEN.value)
        )
        if existing is not None:
            existing.status = CardStatus.SUPERSEDED.value
            existing.resolved_at = clock.now()
            session.flush()
        return None

    product = session.scalar(select(Product.name).where(Product.id == contract.product_id)) or "—"
    name = session.scalar(select(Counterparty.name).where(Counterparty.id == contract.counterparty_id)) or "—"
    shortage = -m.remaining_t
    payload = ContractOverPayload(
        contract_id=contract.id,
        contract_number=contract.number,
        product=product,
        remaining_t=m.remaining_t.quantize(T3),
        requested_t=m.requested_t.quantize(T3),
        shortage_t=shortage.quantize(T3),
    )
    card, created = CardService.create_or_get(
        session,
        CardKind.CONTRACT_OVER,
        title=f"Договорът с {name} не стига",
        subtitle=f"№ {contract.number}: не стигат {shortage.quantize(T3)} т",
        payload=payload,
        dedupe_key=key,
        contract_id=contract.id,
        counterparty_id=contract.counterparty_id,
    )
    if not created:
        card.payload = payload.model_dump(mode="json")
        card.subtitle = f"№ {contract.number}: не стигат {shortage.quantize(T3)} т"
        session.flush()
    return card
