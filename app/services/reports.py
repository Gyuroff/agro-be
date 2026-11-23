"""Справка от доставчик: решения по редове и потвърждение (docs/api.md §2 и §4, data-model.md §8–§9).

- `substitute_accept` / `other_client` само записват `resolution` на непланирания ред — курсовете се
  пипат едва при `confirm`, в една транзакция (`ReportService.confirm`).
- Статусите на курсовете се менят само през `TripService`.
- Стоката на нерешен непланиран ред (в справката няма колона за стока) е стоката на покупните договори
  на доставчика, ако е една; иначе 422 `product_unknown` и картата остава отворена (решение на собственика,
  `bd note agro-br9.7`).
- `InvoiceDraftBuilder` (agro-66f.1) още не съществува: при `billing_mode=per_trip` чернова не се прави.
"""

from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.errors import DomainError
from app.models.cards import ActionCard, CardKind, CardResolution, CardStatus
from app.models.contracts import Contract, ContractSide
from app.models.reports import LineResolution, MatchStatus, ReportStatus, SupplierReport, SupplierReportLine
from app.models.trips import Trip, TripOrigin
from app.services.cards import ActionResult, CardService, register_action
from app.services.contract_alerts import check_over
from app.services.report_matching import counts_as_matched
from app.services.trips import TripService


def _parse[T: BaseModel](model: type[T], body: dict[str, Any]) -> T:
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        fields = {".".join(map(str, e["loc"])): e["msg"] for e in exc.errors()}
        raise DomainError("validation_error", "Невалидни данни.", http_status=400, fields=fields) from exc


class _SubstituteBody(BaseModel):
    unplanned_line_id: int


class _OtherClientBody(BaseModel):
    unplanned_line_id: int
    sale_contract_id: int = Field(gt=0)


def _lines(session: Session, report: SupplierReport) -> list[SupplierReportLine]:
    return list(
        session.scalars(
            select(SupplierReportLine)
            .where(SupplierReportLine.report_id == report.id)
            .order_by(SupplierReportLine.id)
        )
    )


def supplier_product_ids(session: Session, supplier_id: int) -> set[int]:
    return set(
        session.scalars(
            select(Contract.product_id).where(
                Contract.counterparty_id == supplier_id, Contract.side == ContractSide.PURCHASE.value
            )
        )
    )


class ReportService:
    # ── помощни ────────────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def pending_report(session: Session, card: ActionCard) -> SupplierReport:
        report = session.get(SupplierReport, card.supplier_report_id) if card.supplier_report_id else None
        if report is None:
            raise DomainError("not_found", "Справката не е намерена.", http_status=404)
        if report.status != ReportStatus.PENDING.value:
            raise DomainError("card_not_open", "Справката вече е обработена.", http_status=409)
        return report

    @staticmethod
    def _unplanned_line(session: Session, report: SupplierReport, line_id: int) -> SupplierReportLine:
        line = session.get(SupplierReportLine, line_id)
        if line is None or line.report_id != report.id:
            raise DomainError("not_found", "Редът не е намерен.", http_status=404)
        if line.match_status != MatchStatus.UNPLANNED.value:
            raise DomainError(
                "validation_error", "Редът не е непланиран.", 400, {"unplanned_line_id": "Не е непланиран"}
            )
        return line

    # ── решения по ред ───────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def substitute_accept(session: Session, report: SupplierReport, unplanned_line_id: int) -> None:
        line = ReportService._unplanned_line(session, report, unplanned_line_id)
        missing = (
            session.get(SupplierReportLine, line.substitute_for_line_id)
            if line.substitute_for_line_id
            else None
        )
        if missing is None or missing.trip_id is None:
            raise DomainError(
                "validation_error",
                "За този ред няма предложение за заместване.",
                400,
                {"unplanned_line_id": "Няма предложение"},
            )
        line.resolution = LineResolution.SUBSTITUTE_ACCEPTED.value
        line.resolved_contract_id = None
        session.flush()

    @staticmethod
    def other_client(session: Session, report: SupplierReport, unplanned_line_id: int, sale_id: int) -> None:
        line = ReportService._unplanned_line(session, report, unplanned_line_id)
        sale = session.get(Contract, sale_id)
        if sale is None:
            raise DomainError("not_found", "Договорът не е намерен.", http_status=404)
        if sale.side != ContractSide.SALE.value:
            raise DomainError(
                "validation_error", "Договорът не е продажба.", 400, {"sale_contract_id": "Не е продажба"}
            )
        if sale.product_id not in ReportService._line_products(session, report, line):
            raise DomainError(
                "validation_error",
                "Договорът не е за същата стока.",
                400,
                {"sale_contract_id": "Друга стока"},
            )
        if sale.supply_contract_id is None:
            raise DomainError(
                "validation_error",
                "Договорът няма насрещен договор за покупка.",
                400,
                {"sale_contract_id": "Няма договор за покупка"},
            )
        line.resolution = LineResolution.OTHER_CLIENT.value
        line.resolved_contract_id = sale.id
        session.flush()

    @staticmethod
    def _line_products(session: Session, report: SupplierReport, line: SupplierReportLine) -> set[int]:
        """Допустими стоки на непланиран ред: на замествания курс, иначе стоките на доставчика."""
        if line.substitute_for_line_id is not None:
            missing = session.get(SupplierReportLine, line.substitute_for_line_id)
            trip = session.get(Trip, missing.trip_id) if missing and missing.trip_id else None
            if trip is not None:
                return {trip.product_id}
        return supplier_product_ids(session, report.supplier_id)

    # ── потвърждение ─────────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def confirm(session: Session, card: ActionCard) -> ActionResult:
        report = ReportService.pending_report(session, card)
        trips = TripService(session)
        lines = _lines(session, report)
        by_id = {line.id: line for line in lines}
        touched: list[Trip] = []
        completed = 0

        for line in lines:
            if counts_as_matched(line) and line.trip_id is not None:
                assert line.qty_t is not None
                trip = session.get(Trip, line.trip_id)
                assert trip is not None
                trips.complete(
                    trip,
                    actual_qty_t=line.qty_t,
                    load_date=line.load_date or trip.planned_date,
                    supplier_report_line_id=line.id,
                )
                touched.append(trip)
                completed += 1

        for line in lines:
            if line.match_status != MatchStatus.UNPLANNED.value:
                continue
            if line.qty_t is None or line.load_date is None or not line.plate_raw:
                raise DomainError(
                    "validation_error",
                    "Непланиран ред няма номер, дата или количество.",
                    400,
                    {"unplanned_line_id": str(line.id)},
                )
            if line.resolution == LineResolution.SUBSTITUTE_ACCEPTED.value:
                missing = by_id.get(line.substitute_for_line_id or 0)
                trip = session.get(Trip, missing.trip_id) if missing and missing.trip_id else None
                if trip is None:
                    raise DomainError("validation_error", "Липсва курсът за заместване.", 400)
                trips.complete(
                    trip,
                    actual_qty_t=line.qty_t,
                    load_date=line.load_date,
                    plate=line.plate_raw,
                    supplier_report_line_id=line.id,
                )
                if line.warehouse_id is not None:
                    trip.warehouse_id = line.warehouse_id
                line.trip_id = trip.id
                touched.append(trip)
                completed += 1
            elif line.resolution == LineResolution.OTHER_CLIENT.value:
                new = ReportService._create_trip(
                    session, trips, report, line, TripOrigin.REPORT_OTHER_CLIENT, line.resolved_contract_id
                )
                touched.append(new)
                completed += 1
            else:
                line.resolution = LineResolution.LEFT_UNPLANNED.value
                touched.append(
                    ReportService._create_trip(
                        session, trips, report, line, TripOrigin.REPORT_UNPLANNED, None
                    )
                )

        report.status = ReportStatus.CONFIRMED.value
        report.confirmed_at = clock.now()
        CardService.resolve(session, card, CardStatus.DONE, CardResolution.CONFIRMED)

        contract_ids = {
            cid for t in touched for cid in (t.sale_contract_id, t.purchase_contract_id) if cid is not None
        }
        for contract_id in sorted(contract_ids):
            contract = session.get(Contract, contract_id)
            if contract is not None:
                check_over(session, contract)
        session.flush()
        return ActionResult(f"Отбелязани като изпълнени: {completed}")

    @staticmethod
    def _create_trip(
        session: Session,
        trips: TripService,
        report: SupplierReport,
        line: SupplierReportLine,
        origin: TripOrigin,
        sale_contract_id: int | None,
    ) -> Trip:
        assert line.qty_t is not None and line.load_date is not None and line.plate_raw is not None
        if sale_contract_id is not None:
            sale = session.get(Contract, sale_contract_id)
            assert sale is not None
            product_id = sale.product_id
        else:
            products = supplier_product_ids(session, report.supplier_id)
            if len(products) != 1:
                raise DomainError(
                    "product_unknown",
                    "Стоката на непланирания курс не може да се определи — реши реда с „Друг клиент“.",
                    http_status=422,
                )
            product_id = next(iter(products))
        trip = trips.create_unplanned(
            origin=origin,
            supplier_id=report.supplier_id,
            product_id=product_id,
            plate=line.plate_raw,
            planned_date=line.load_date,
            actual_qty_t=line.qty_t,
            load_date=line.load_date,
            supplier_report_line_id=line.id,
            driver_name=line.driver_name,
            warehouse_id=line.warehouse_id,
        )
        if sale_contract_id is not None:
            trips.link_to_contract(trip, sale_contract_id)
        line.trip_id = trip.id
        return trip


# ── действия по карта (docs/api.md §2) ────────────────────────────────────────────────────────────────
def _substitute_accept(session: Session, card: ActionCard, body: dict[str, Any]) -> ActionResult:
    data = _parse(_SubstituteBody, body)
    ReportService.substitute_accept(
        session, ReportService.pending_report(session, card), data.unplanned_line_id
    )
    return ActionResult("Заместването е прието")


def _other_client(session: Session, card: ActionCard, body: dict[str, Any]) -> ActionResult:
    data = _parse(_OtherClientBody, body)
    ReportService.other_client(
        session, ReportService.pending_report(session, card), data.unplanned_line_id, data.sale_contract_id
    )
    return ActionResult("Курсът е отнесен към другия клиент")


register_action(CardKind.SUPPLIER_REPORT, "substitute_accept", _substitute_accept)
register_action(CardKind.SUPPLIER_REPORT, "other_client", _other_client)
register_action(CardKind.SUPPLIER_REPORT, "confirm", lambda s, c, _b: ReportService.confirm(s, c))
