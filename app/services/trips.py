"""TripService — единственото място, което сменя `trips.status` (docs/data-model.md §8, „Преходи“).

Всеки преход пише `TripEvent` с готов текст. Забранен преход → `DomainError` 409 `invalid_transition`.
Картите и имейлите са в други задачи; тук са само преходите и историята.

Курс от справка без договор (`unplanned`) няма преход към събитие в спецификацията: видовете събития
нямат подходящ за него, затова `create_unplanned` не пише събитие (виж `bd note agro-l2v.7`).
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core import clock
from app.core.errors import DomainError
from app.domain.plates import format_plate, normalize_plate
from app.models.contracts import Contract, ContractSide
from app.models.masterdata import Counterparty
from app.models.trips import (
    CancelSource,
    Trip,
    TripEvent,
    TripEventKind,
    TripOrigin,
    TripStatus,
)
from app.services.contracts import price_at


class InvalidTransition(DomainError):
    def __init__(self, current: str, target: TripStatus) -> None:
        super().__init__(
            "invalid_transition",
            f"Курс със статус „{current}“ не може да стане „{target.value}“.",
            http_status=409,
        )


@dataclass(frozen=True)
class PlannedTripData:
    """Какво е нужно, за да се създаде планиран курс от ред на заявка."""

    request_id: int
    sale_contract: Contract
    purchase_contract: Contract
    planned_date: date
    plate: str
    planned_qty_t: Decimal
    driver_name: str | None = None
    driver_phone: str | None = None
    inbound_email_id: int | None = None
    outbound_email_id: int | None = None


def _qty(value: Decimal) -> str:
    return f"{value:.2f}".replace(".", ",")


def _name(session: Session, counterparty_id: int) -> str:
    cp = session.get(Counterparty, counterparty_id)
    return cp.name if cp is not None else "—"


def _event(
    session: Session,
    trip: Trip,
    kind: TripEventKind,
    text: str,
    *,
    inbound_email_id: int | None = None,
    outbound_email_id: int | None = None,
) -> TripEvent:
    event = TripEvent(
        trip_id=trip.id,
        occurred_at=clock.now(),
        kind=kind.value,
        text=text,
        inbound_email_id=inbound_email_id,
        outbound_email_id=outbound_email_id,
    )
    session.add(event)
    return event


def _require(trip: Trip, allowed: set[TripStatus], target: TripStatus) -> None:
    if trip.status not in {s.value for s in allowed}:
        raise InvalidTransition(trip.status, target)


class TripService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_planned(self, data: PlannedTripData) -> Trip:
        """— → `planned` (от `RequestService.finalize()`); пише `requested` и (с писмо) `sent_to_supplier`."""
        sale, purchase = data.sale_contract, data.purchase_contract
        trip = Trip(
            status=TripStatus.PLANNED.value,
            origin=TripOrigin.REQUEST.value,
            request_id=data.request_id,
            sale_contract_id=sale.id,
            purchase_contract_id=purchase.id,
            customer_id=sale.counterparty_id,
            supplier_id=purchase.counterparty_id,
            product_id=sale.product_id,
            warehouse_id=purchase.warehouse_id,
            planned_date=data.planned_date,
            plate_norm=normalize_plate(data.plate),
            plate_display=format_plate(data.plate),
            driver_name=data.driver_name,
            driver_phone=data.driver_phone,
            planned_qty_t=data.planned_qty_t,
        )
        self.session.add(trip)
        self.session.flush()
        _event(
            self.session,
            trip,
            TripEventKind.REQUESTED,
            f"Заявен — имейл от {_name(self.session, sale.counterparty_id)}",
            inbound_email_id=data.inbound_email_id,
        )
        if data.outbound_email_id is not None:
            _event(
                self.session,
                trip,
                TripEventKind.SENT_TO_SUPPLIER,
                f"Изпратен към {_name(self.session, purchase.counterparty_id)}",
                outbound_email_id=data.outbound_email_id,
            )
        self.session.flush()
        return trip

    def create_unplanned(
        self,
        *,
        origin: TripOrigin,
        supplier_id: int,
        product_id: int,
        plate: str,
        planned_date: date,
        actual_qty_t: Decimal,
        load_date: date,
        supplier_report_line_id: int | None = None,
        driver_name: str | None = None,
        warehouse_id: int | None = None,
    ) -> Trip:
        """— → `unplanned` (от `ReportService.confirm()` за ред без съвпадение и без решение)."""
        if origin is TripOrigin.REQUEST:
            raise DomainError("validation_error", "Непланиран курс не може да е от заявка.", 400)
        trip = Trip(
            status=TripStatus.UNPLANNED.value,
            origin=origin.value,
            supplier_id=supplier_id,
            product_id=product_id,
            warehouse_id=warehouse_id,
            planned_date=planned_date,
            plate_norm=normalize_plate(plate),
            plate_display=format_plate(plate),
            driver_name=driver_name,
            planned_qty_t=actual_qty_t,
            actual_qty_t=actual_qty_t,
            load_date=load_date,
            supplier_report_line_id=supplier_report_line_id,
        )
        self.session.add(trip)
        self.session.flush()
        return trip

    def complete(
        self,
        trip: Trip,
        *,
        actual_qty_t: Decimal,
        load_date: date,
        plate: str | None = None,
        supplier_report_line_id: int | None = None,
    ) -> Trip:
        """`planned` → `completed` (`ReportService.confirm()`): тонаж, дата, цени от `price_at(load_date)`."""
        _require(trip, {TripStatus.PLANNED}, TripStatus.COMPLETED)
        assert trip.sale_contract_id is not None and trip.purchase_contract_id is not None
        prices = self._prices(trip.sale_contract_id, trip.purchase_contract_id, load_date)
        if plate is not None and normalize_plate(plate) != trip.plate_norm:
            old = trip.plate_display
            trip.original_plate_norm = trip.plate_norm
            trip.plate_norm = normalize_plate(plate)
            trip.plate_display = format_plate(plate)
            _event(
                self.session,
                trip,
                TripEventKind.PLATE_CHANGED,
                f"Сменен камион — {old} → {trip.plate_display}",
            )
        self._finish(trip, actual_qty_t, load_date, prices, supplier_report_line_id)
        return trip

    def link_unplanned(self, trip: Trip, *, sale_contract: Contract, purchase_contract: Contract) -> Trip:
        """`unplanned` → `completed` („Свържи с договор“, екран 6). Количество и дата са от справката."""
        _require(trip, {TripStatus.UNPLANNED}, TripStatus.COMPLETED)
        if trip.actual_qty_t is None or trip.load_date is None:
            raise DomainError("validation_error", "Курсът няма данни от справка.", 400)
        prices = self._prices(sale_contract.id, purchase_contract.id, trip.load_date)
        trip.sale_contract_id = sale_contract.id
        trip.purchase_contract_id = purchase_contract.id
        trip.customer_id = sale_contract.counterparty_id
        trip.warehouse_id = purchase_contract.warehouse_id or trip.warehouse_id
        _event(
            self.session,
            trip,
            TripEventKind.LINKED,
            f"Свързан с договор {sale_contract.number}",
        )
        self._finish(trip, trip.actual_qty_t, trip.load_date, prices, trip.supplier_report_line_id)
        return trip

    def cancel(self, trip: Trip, *, source: CancelSource, reason: str | None = None) -> Trip:
        """`planned` → `cancelled` (карта „Отказани курсове“ или „Маркирай курса като отказан“)."""
        _require(trip, {TripStatus.PLANNED}, TripStatus.CANCELLED)
        trip.status = TripStatus.CANCELLED.value
        trip.cancelled_at = clock.now()
        trip.cancel_source = source.value
        trip.cancel_reason = reason
        who = "по имейл от доставчика" if source is CancelSource.EMAIL else "от потребител"
        text = f"Отказан {who}" + (f" — {reason}" if reason else "")
        _event(self.session, trip, TripEventKind.CANCELLED, text)
        self.session.flush()
        return trip

    def link_to_contract(self, trip: Trip, sale_contract_id: int) -> Trip:
        """`link_unplanned` по id на продажния договор; покупният е `supply_contract` на продажния."""
        sale = self.session.get(Contract, sale_contract_id)
        if sale is None:
            raise DomainError("not_found", "Договорът не е намерен.", http_status=404)
        if sale.side != ContractSide.SALE.value or sale.product_id != trip.product_id:
            raise DomainError("validation_error", "Договорът не е продажба на същата стока.", 400)
        purchase = self.session.get(Contract, sale.supply_contract_id) if sale.supply_contract_id else None
        if purchase is None:
            raise DomainError("validation_error", "Договорът няма насрещен договор за покупка.", 400)
        return self.link_unplanned(trip, sale_contract=sale, purchase_contract=purchase)

    def _prices(
        self, sale_contract_id: int, purchase_contract_id: int, load_date: date
    ) -> tuple[Decimal, Decimal]:
        """Цените към датата на натоварване. Вика се ПРЕДИ да се пипне курсът: `NoPriceForDate` е 422."""
        return (
            price_at(self.session, sale_contract_id, load_date),
            price_at(self.session, purchase_contract_id, load_date),
        )

    def _finish(
        self,
        trip: Trip,
        actual_qty_t: Decimal,
        load_date: date,
        prices: tuple[Decimal, Decimal],
        report_line_id: int | None,
    ) -> None:
        trip.status = TripStatus.COMPLETED.value
        trip.actual_qty_t = actual_qty_t
        trip.load_date = load_date
        trip.sale_price_per_t, trip.purchase_price_per_t = prices
        trip.completed_at = clock.now()
        if report_line_id is not None:
            trip.supplier_report_line_id = report_line_id
        _event(
            self.session,
            trip,
            TripEventKind.COMPLETED,
            f"Изпълнен — {_qty(actual_qty_t)} т, натоварен {load_date.strftime('%d.%m.%Y')}",
        )
        self.session.flush()
