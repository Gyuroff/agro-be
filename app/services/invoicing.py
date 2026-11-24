"""Чернова на фактура (docs/data-model.md §11): `InvoiceDraftBuilder` и ежедневното фактуриране.

Курсовете във фактурата са `completed`, без `trips.invoice_id` и без друга неанулирана чернова/фактура
(решение на собственика: курс от минала чернова не се брои втори път); ред = (договор, стока, цена).
Пари: само `Decimal` през `app/domain/money`.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.errors import DomainError
from app.domain.billing import billing_dates, billing_label, previous_billing_date
from app.domain.money import line_amount, q2, q3, vat
from app.models.cards import CardKind
from app.models.company import CompanySettings
from app.models.contracts import Contract, ContractSide, ContractStatus
from app.models.invoicing import Invoice, InvoiceLine, InvoiceMode, InvoiceStatus, InvoiceTrip
from app.models.masterdata import Counterparty, Product
from app.models.trips import Trip, TripStatus
from app.schemas.cards import InvoiceDueLine, InvoiceDuePayload
from app.services.cards import CardService

PER_TRIP = "per_trip"
PER_PERIOD = "per_period"


@dataclass(frozen=True)
class EffectiveBilling:
    mode: str
    rrule: str | None
    dtstart: date


def effective_billing(contract: Contract, customer: Counterparty) -> EffectiveBilling | None:
    """Режимът и графикът на договора, иначе на контрагента; `None` — не се фактурира автоматично."""
    mode = contract.billing_mode or customer.default_billing_mode
    if mode is None:
        return None
    rrule = contract.billing_rrule or customer.default_billing_rrule
    return EffectiveBilling(mode, rrule, contract.billing_dtstart or contract.valid_from)


def _find_cycle(
    session: Session, customer_id: int, sale_contract_id: int | None, billing_date: date
) -> Invoice | None:
    stmt = select(Invoice).where(
        Invoice.counterparty_id == customer_id,
        Invoice.billing_date == billing_date,
        Invoice.status != InvoiceStatus.VOID.value,
    )
    stmt = stmt.where(
        Invoice.sale_contract_id.is_(None)
        if sale_contract_id is None
        else Invoice.sale_contract_id == sale_contract_id
    )
    return session.scalar(stmt)


class InvoiceDraftBuilder:
    def __init__(self, session: Session) -> None:
        self.session = session

    def build(
        self,
        customer: Counterparty,
        billing_date: date,
        sale_contract: Contract | None = None,
        *,
        per_trip: bool = False,
    ) -> Invoice | None:
        """Чернова за цикъла (клиент, договор или всички, ден). Идемпотентна: черновата се преизчислява.

        `None` — няма курсове за фактуриране (съществуваща чернова се анулира). Издадена фактура за
        цикъла се връща непроменена. `per_trip` включва и курсовете от самия ден (`load_date <= D`).
        """
        if customer.merge_contracts_in_invoice:
            sale_contract = None
        elif sale_contract is None:
            raise DomainError(
                "validation_error", "Без обединяване на договорите фактурата е по договор.", 400
            )
        elif sale_contract.side != ContractSide.SALE.value or sale_contract.counterparty_id != customer.id:
            raise DomainError("validation_error", "Договорът не е продажен договор на клиента.", 400)
        contract_id = sale_contract.id if sale_contract is not None else None

        existing = _find_cycle(self.session, customer.id, contract_id, billing_date)
        if existing is not None and existing.status != InvoiceStatus.DRAFT.value:
            return existing

        trips = self._trips(customer, contract_id, billing_date, per_trip, existing)
        if not trips:
            if existing is not None:
                existing.status = InvoiceStatus.VOID.value
                self.session.flush()
            return None

        groups: dict[tuple[int, int, Decimal], list[Trip]] = defaultdict(list)
        for trip in trips:
            assert trip.sale_contract_id is not None and trip.sale_price_per_t is not None
            groups[(trip.sale_contract_id, trip.product_id, trip.sale_price_per_t)].append(trip)

        company = self.session.get_one(CompanySettings, 1)
        rate = company.default_vat_rate
        rows = self.session.execute(
            select(Product.id, Product.name).where(Product.id.in_({k[1] for k in groups}))
        ).all()
        names = {row[0]: row[1] for row in rows}
        lines: list[dict[str, object]] = []
        subtotal = Decimal(0)
        for (cid, product_id, price), members in sorted(groups.items()):
            qty = q3(sum((t.actual_qty_t or Decimal(0) for t in members), Decimal(0)))
            amount = line_amount(qty, price)
            subtotal += amount
            lines.append(
                {
                    "product_id": product_id,
                    "sale_contract_id": cid,
                    "description": names.get(product_id, "—"),
                    "qty_t": qty,
                    "unit_price": price,
                    "amount": amount,
                    "vat_rate": rate,
                    "vat_amount": vat(amount, rate),
                }
            )
        subtotal = q2(subtotal)
        vat_amount = vat(subtotal, rate)  # ДДС върху данъчната основа на цялата фактура

        loads = [t.load_date for t in trips if t.load_date is not None]
        period_to = billing_date if per_trip else billing_date - timedelta(days=1)
        period_from = min(loads)
        if not per_trip:
            prev = self._previous_date(customer, sale_contract, billing_date)
            if prev is not None:
                period_from = min(period_from, prev)

        invoice = existing or Invoice(
            counterparty_id=customer.id,
            sale_contract_id=contract_id,
            billing_date=billing_date,
            mode=customer.invoicing_mode,
            subtotal=subtotal,
            vat_amount=vat_amount,
            total=q2(subtotal + vat_amount),
            period_from=period_from,
            period_to=period_to,
        )
        invoice.mode = InvoiceMode(customer.invoicing_mode).value
        invoice.period_from, invoice.period_to = period_from, period_to
        invoice.subtotal, invoice.vat_amount, invoice.total = subtotal, vat_amount, q2(subtotal + vat_amount)
        self._snapshots(invoice, customer, company)
        terms = (sale_contract.payment_terms_days if sale_contract else None) or (
            customer.default_payment_terms_days
        )
        invoice.due_date = billing_date + timedelta(days=terms) if terms is not None else None
        if existing is None:
            self.session.add(invoice)
        self.session.flush()

        self._replace_children(invoice, lines, trips)
        self.session.flush()
        return invoice

    def _trips(
        self,
        customer: Counterparty,
        contract_id: int | None,
        billing_date: date,
        per_trip: bool,
        existing: Invoice | None,
    ) -> list[Trip]:
        elsewhere = (
            select(InvoiceTrip.trip_id)
            .join(Invoice, Invoice.id == InvoiceTrip.invoice_id)
            .where(Invoice.status != InvoiceStatus.VOID.value)
        )
        if existing is not None:
            elsewhere = elsewhere.where(Invoice.id != existing.id)
        stmt = select(Trip).where(
            Trip.status == TripStatus.COMPLETED.value,
            Trip.customer_id == customer.id,
            Trip.invoice_id.is_(None),
            Trip.id.not_in(elsewhere),
            Trip.load_date <= billing_date if per_trip else Trip.load_date < billing_date,
        )
        if contract_id is not None:
            stmt = stmt.where(Trip.sale_contract_id == contract_id)
        return list(self.session.scalars(stmt.order_by(Trip.load_date, Trip.id)))

    def _previous_date(
        self, customer: Counterparty, sale_contract: Contract | None, billing_date: date
    ) -> date | None:
        contract = sale_contract
        if contract is None:
            contract = self.session.scalars(
                select(Contract)
                .where(Contract.counterparty_id == customer.id, Contract.side == ContractSide.SALE.value)
                .order_by(Contract.id)
            ).first()
        eff = effective_billing(contract, customer) if contract is not None else None
        if eff is None or eff.rrule is None:
            return None
        return previous_billing_date(eff.rrule, eff.dtstart, billing_date)

    @staticmethod
    def _snapshots(invoice: Invoice, customer: Counterparty, company: CompanySettings) -> None:
        invoice.recipient_name = customer.legal_name
        invoice.recipient_eik = customer.eik
        invoice.recipient_vat_number = customer.vat_number
        invoice.recipient_address = ", ".join(p for p in (customer.address_city, customer.address_line) if p)
        invoice.recipient_mol = customer.mol
        invoice.recipient_email = customer.invoice_email
        invoice.supplier_legal_name = company.legal_name
        invoice.supplier_eik = company.eik
        invoice.supplier_vat_number = company.vat_number
        invoice.supplier_address = ", ".join(p for p in (company.address_city, company.address_line) if p)
        invoice.supplier_mol = company.mol
        invoice.supplier_iban = company.iban

    def _replace_children(
        self, invoice: Invoice, lines: Sequence[dict[str, object]], trips: Sequence[Trip]
    ) -> None:
        for old_line in self.session.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id)):
            self.session.delete(old_line)
        for old_link in self.session.scalars(select(InvoiceTrip).where(InvoiceTrip.invoice_id == invoice.id)):
            self.session.delete(old_link)
        self.session.flush()  # изтриването е преди добавянето: UNIQUE (invoice_id, line_no)
        for no, data in enumerate(lines, start=1):
            self.session.add(InvoiceLine(invoice_id=invoice.id, line_no=no, **data))  # type: ignore[arg-type]
        for trip in trips:
            self.session.add(InvoiceTrip(invoice_id=invoice.id, trip_id=trip.id))


def due_card_payload(session: Session, invoice: Invoice, customer: Counterparty) -> InvoiceDuePayload:
    lines = list(
        session.scalars(
            select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id).order_by(InvoiceLine.line_no)
        )
    )
    trip_count = len(list(session.scalars(select(InvoiceTrip).where(InvoiceTrip.invoice_id == invoice.id))))
    contract = session.get(Contract, invoice.sale_contract_id) if invoice.sale_contract_id else None
    eff = effective_billing(contract, customer) if contract is not None else None
    try:
        label = billing_label(eff.mode, eff.rrule) if eff is not None else ""
    except ValueError:
        label = ""
    return InvoiceDuePayload(
        invoice_id=invoice.id,
        invoice_version=invoice.version,
        period_from=invoice.period_from,
        period_to=invoice.period_to,
        schedule_label=label,
        trip_count=trip_count,
        lines=[
            InvoiceDueLine(
                product=line.description, qty_t=line.qty_t, unit_price=line.unit_price, amount=line.amount
            )
            for line in lines
        ],
        vat_rate=lines[0].vat_rate if lines else Decimal(0),
        vat_amount=invoice.vat_amount,
        total=invoice.total,
        due_date=invoice.due_date or invoice.billing_date,
    )


def bill_cycle(
    session: Session,
    customer: Counterparty,
    billing_date: date,
    sale_contract: Contract | None = None,
    *,
    per_trip: bool = False,
) -> Invoice | None:
    """Чернова за цикъла; при `erp` — карта „Време за фактура“ (по `shadow` няма карта)."""
    invoice = InvoiceDraftBuilder(session).build(customer, billing_date, sale_contract, per_trip=per_trip)
    if (
        invoice is None
        or invoice.mode != InvoiceMode.ERP.value
        or invoice.status != InvoiceStatus.DRAFT.value
    ):
        return invoice
    payload = due_card_payload(session, invoice, customer)
    card, created = CardService.create_or_get(
        session,
        CardKind.INVOICE_DUE,
        title=f"Време за фактура към {customer.name}",
        subtitle=f"{payload.trip_count} курса · {payload.total} € с ДДС",
        payload=payload,
        dedupe_key=f"invoice_due:{invoice.id}:{billing_date.isoformat()}",
        invoice_id=invoice.id,
        counterparty_id=customer.id,
        contract_id=invoice.sale_contract_id,
    )
    if not created:
        card.payload = payload.model_dump(mode="json")  # преизчислена чернова → пресни числа
        session.flush()
    return invoice


def run_daily_billing(session: Session, today: date) -> list[int]:
    """Задача `invoicing.daily_billing`: за всеки активен продажен договор с дата за фактуриране днес."""
    contracts = session.scalars(
        select(Contract)
        .where(
            Contract.side == ContractSide.SALE.value,
            Contract.status == ContractStatus.ACTIVE.value,
            Contract.valid_from <= today,
            (Contract.valid_to.is_(None)) | (Contract.valid_to >= today),
        )
        .order_by(Contract.id)
    ).all()
    done: set[tuple[int, int | None]] = set()
    invoice_ids: list[int] = []
    for contract in contracts:
        customer = session.get_one(Counterparty, contract.counterparty_id)
        eff = effective_billing(contract, customer)
        if not customer.is_active or eff is None or eff.mode != PER_PERIOD or not eff.rrule:
            continue
        if not billing_dates(eff.rrule, eff.dtstart, today, today):
            continue
        key = (customer.id, None if customer.merge_contracts_in_invoice else contract.id)
        if key in done:
            continue
        done.add(key)
        invoice = bill_cycle(session, customer, today, contract)
        if invoice is not None:
            invoice_ids.append(invoice.id)
    return invoice_ids


def bill_per_trip(session: Session, trips: Sequence[Trip]) -> list[int]:
    """`ReportService.confirm`: за клиентите с `per_trip` — една чернова за деня с новоизпълнените курсове."""
    today = clock.today_sofia()
    done: set[tuple[int, int | None]] = set()
    invoice_ids: list[int] = []
    for trip in trips:
        if trip.status != TripStatus.COMPLETED.value or trip.sale_contract_id is None:
            continue
        contract = session.get_one(Contract, trip.sale_contract_id)
        customer = session.get_one(Counterparty, contract.counterparty_id)
        eff = effective_billing(contract, customer)
        if eff is None or eff.mode != PER_TRIP:
            continue
        key = (customer.id, None if customer.merge_contracts_in_invoice else contract.id)
        if key in done:
            continue
        done.add(key)
        invoice = bill_cycle(session, customer, today, contract, per_trip=True)
        if invoice is not None:
            invoice_ids.append(invoice.id)
    return invoice_ids
