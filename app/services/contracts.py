"""Договори: валидации при запис (за админа и сийда) и цена към дата (docs/data-model.md §6)."""

from datetime import date
from decimal import Decimal

from dateutil.rrule import rrulestr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.domain.pricing import NoPriceForDate
from app.models.contracts import Contract, ContractPrice, ContractSide
from app.models.masterdata import Contact


class ContractValidationError(DomainError):
    def __init__(self, field: str, message_bg: str) -> None:
        super().__init__("validation_error", message_bg, http_status=400)
        self.field = field


def validate_contract(session: Session, contract: Contract) -> None:
    """Проверките, които CHECK-овете в базата не могат да направят (връзки между редове, RRULE).

    Извиква се от админа и сийда преди flush; `ContractValidationError` носи името на полето.
    """
    if contract.supply_contract_id is not None:
        supply = session.get(Contract, contract.supply_contract_id)
        if supply is None or supply.side != ContractSide.PURCHASE.value:
            raise ContractValidationError("supply_contract_id", "Насрещният договор трябва да е за покупка.")
        if supply.product_id != contract.product_id:
            raise ContractValidationError(
                "supply_contract_id", "Насрещният договор трябва да е за същата стока."
            )

    if contract.order_contact_id is not None:
        contact = session.get(Contact, contract.order_contact_id)
        if contact is None or contact.counterparty_id != contract.counterparty_id:
            raise ContractValidationError(
                "order_contact_id", "Контактът за поръчки трябва да е на същия контрагент."
            )
        if not contact.receives_orders:
            raise ContractValidationError(
                "order_contact_id", "Контактът не е отбелязан като получател на поръчки."
            )

    if contract.billing_rrule is not None:
        try:
            rrulestr(contract.billing_rrule)
        except (ValueError, TypeError, KeyError) as exc:
            raise ContractValidationError("billing_rrule", "Невалидно правило за фактуриране.") from exc


def price_at(session: Session, contract_id: int, on: date, packaging: str | None = None) -> Decimal:
    """Цената по договора към `on` (датата на натоварване); `NoPriceForDate` (422), ако няма.

    `packaging=None` — най-новият ред независимо от разфасовката (старият код, докато курсът няма разфасовка).
    """
    stmt = select(ContractPrice.price_per_t).where(
        ContractPrice.contract_id == contract_id, ContractPrice.valid_from <= on
    )
    if packaging is not None:
        stmt = stmt.where(ContractPrice.packaging == packaging)
    price = session.scalar(stmt.order_by(ContractPrice.valid_from.desc()).limit(1))
    if price is None:
        raise NoPriceForDate(contract_id, on)
    return price
