"""Режим сянка: сравнение на черновата с фактурата от Мега (docs/data-model.md §11, api.md §8, agro-66f.4).

Правилото „съвпада ли“ е на бекенда (сума ± `invoice_rounding_tolerance_eur`, тонаж, цена/т, брой курсове);
`agent_explanation` е детерминиран текст, докато агентът `invoice_comparator` (agro-6p5.8) не го заменя.
"""

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.audit_context import get_audit_context
from app.core.errors import DomainError
from app.domain.dates import add_working_days
from app.domain.money import q2
from app.models.cards import ActionCard, CardKind, CardResolution, CardStatus
from app.models.company import CompanySettings
from app.models.invoicing import (
    ComparisonResolution,
    ComparisonResult,
    Invoice,
    InvoiceComparison,
    InvoiceLine,
    InvoiceMode,
    InvoiceStatus,
    InvoiceTrip,
    MegaInvoice,
)
from app.models.mail import OutboundEmail, OutboundPurpose
from app.models.masterdata import Counterparty
from app.models.trips import Trip, TripEvent, TripEventKind
from app.schemas.cards import ComparisonRow, InvoiceMismatchPayload, InvoiceMissingPayload
from app.schemas.invoices import ComparisonDTO, ComparisonRowDTO
from app.services.cards import ActionResult, CardService, register_action
from app.services.outbound import OutboundService

log = logging.getLogger(__name__)

D = Decimal
NO_VALUE = "—"  # Мега не носи брой курсове във фактурата — не го измисляме

KIND_PRICE = "price"
KIND_QUANTITY = "quantity"
KIND_MISSING_TRIP = "missing_trip"
KIND_EXTRA_TRIP = "extra_trip"
KIND_VAT = "vat"
KIND_ROUNDING = "rounding"


@dataclass(frozen=True)
class Verdict:
    result: ComparisonResult
    diff: dict[str, Any]
    kinds: list[str]
    explanation: str | None


def _s(value: Decimal | None, places: int = 2) -> str | None:
    return None if value is None else f"{value:.{places}f}"


def _mega_lines(mega: MegaInvoice) -> list[dict[str, Any]]:
    return [dict(line) for line in mega.lines]


def _price_set(prices: list[Decimal]) -> list[str]:
    return sorted({f"{p:.2f}" for p in prices}, key=D)


def judge(
    invoice: Invoice,
    lines: list[InvoiceLine],
    trip_count: int,
    mega: MegaInvoice,
    tolerance: Decimal,
) -> Verdict:
    """Сравнява черновата с фактурата от Мега. Не пише в базата."""
    m_lines = _mega_lines(mega)
    erp_qty = sum((line.qty_t for line in lines), D(0))
    mega_qty = sum((D(str(line.get("qty_t", 0))) for line in m_lines), D(0))
    erp_prices = _price_set([line.unit_price for line in lines])
    mega_prices = _price_set([D(str(line["unit_price"])) for line in m_lines if "unit_price" in line])
    m_trips = [line["trips"] for line in m_lines if isinstance(line.get("trips"), int)]
    mega_trips = sum(m_trips) if m_trips else None
    diff: dict[str, Any] = {
        "trips": [trip_count, mega_trips],
        "qty_t": [_s(erp_qty, 3), _s(mega_qty, 3)],
        "price_per_t": [erp_prices, mega_prices],
        "subtotal": [_s(invoice.subtotal), _s(mega.subtotal)],
        "vat": [_s(invoice.vat_amount), _s(mega.vat_amount)],
        "total": [_s(invoice.total), _s(mega.total)],
    }
    kinds: list[str] = []
    if mega_trips is not None and mega_trips != trip_count:
        kinds.append(KIND_MISSING_TRIP if mega_trips < trip_count else KIND_EXTRA_TRIP)
    if erp_qty != mega_qty:
        kinds.append(KIND_QUANTITY)
    if erp_prices != mega_prices:
        kinds.append(KIND_PRICE)

    deltas = {
        "subtotal": (mega.subtotal if mega.subtotal is not None else D(0)) - invoice.subtotal,
        "vat": (mega.vat_amount if mega.vat_amount is not None else D(0)) - invoice.vat_amount,
        "total": (mega.total if mega.total is not None else D(0)) - invoice.total,
    }
    if abs(deltas["vat"]) > tolerance and not (set(kinds) & {KIND_PRICE, KIND_QUANTITY}):
        kinds.append(KIND_VAT)
    money_off = any(abs(d) > tolerance for d in deltas.values())
    if not money_off and any(d != 0 for d in deltas.values()):
        kinds.append(KIND_ROUNDING)
    elif money_off and not kinds:
        kinds.append(KIND_VAT if abs(deltas["vat"]) > tolerance else KIND_ROUNDING)

    mismatch = money_off or bool(set(kinds) - {KIND_ROUNDING})
    result = ComparisonResult.MISMATCH if mismatch else ComparisonResult.MATCH
    return Verdict(result, diff, kinds, explain(kinds, diff) if mismatch else None)


def explain(kinds: list[str], diff: dict[str, Any]) -> str:
    parts: list[str] = []
    if KIND_PRICE in kinds:
        parts.append(
            f"цената в Мега е {', '.join(diff['price_per_t'][1])} "
            f"вместо {', '.join(diff['price_per_t'][0])} €/т"
        )
    if KIND_QUANTITY in kinds:
        parts.append(f"тонажът в Мега е {diff['qty_t'][1]} вместо {diff['qty_t'][0]} т")
    if KIND_MISSING_TRIP in kinds:
        parts.append(f"в Мега липсват курсове ({diff['trips'][1]} вместо {diff['trips'][0]})")
    if KIND_EXTRA_TRIP in kinds:
        parts.append(f"в Мега има повече курсове ({diff['trips'][1]} вместо {diff['trips'][0]})")
    if KIND_VAT in kinds:
        parts.append(f"ДДС в Мега е {diff['vat'][1]} вместо {diff['vat'][0]}")
    if not parts:
        parts.append("разликата в сумата надвишава допустимото закръгляване")
    return "Вероятна причина: " + "; ".join(parts) + "."


def _diff_str(erp: str | None, mega: str | None) -> str | None:
    if erp is None or mega is None:
        return None
    return f"{D(mega) - D(erp):.2f}"


def rows_from_diff(diff: dict[str, Any], kinds: list[str]) -> list[ComparisonRowDTO]:
    """Редовете на екран 16 — от записания `diff`, без нови изчисления."""
    if not diff:
        return []
    trips = diff["trips"]
    qty = diff["qty_t"]
    prices = diff["price_per_t"]
    sub = diff["subtotal"]
    rows = [
        ComparisonRowDTO(
            label="Курсове",
            erp=str(trips[0]),
            mega=NO_VALUE if trips[1] is None else str(trips[1]),
            diff=None if trips[1] is None or trips[1] == trips[0] else str(trips[1] - trips[0]),
            ok=KIND_MISSING_TRIP not in kinds and KIND_EXTRA_TRIP not in kinds,
        ),
        ComparisonRowDTO(
            label="Тонаж",
            erp=qty[0],
            mega=qty[1],
            diff=None if qty[0] == qty[1] else _diff_str(qty[0], qty[1]),
            ok=KIND_QUANTITY not in kinds,
        ),
        ComparisonRowDTO(
            label="Цена / т",
            erp=", ".join(prices[0]),
            mega=", ".join(prices[1]),
            diff=_diff_str(prices[0][0], prices[1][0])
            if len(prices[0]) == 1 and len(prices[1]) == 1 and prices[0] != prices[1]
            else None,
            ok=KIND_PRICE not in kinds,
        ),
        ComparisonRowDTO(
            label="Без ДДС",
            erp=sub[0],
            mega=sub[1] or NO_VALUE,
            diff=None if sub[0] == sub[1] else _diff_str(sub[0], sub[1]),
            ok=sub[0] == sub[1] or KIND_ROUNDING in kinds,
        ),
    ]
    if KIND_VAT in kinds:
        vat = diff["vat"]
        rows.append(
            ComparisonRowDTO(label="ДДС", erp=vat[0], mega=vat[1] or NO_VALUE, diff=_diff_str(*vat), ok=False)
        )
    return rows


def _card_rows(rows: list[ComparisonRowDTO]) -> list[ComparisonRow]:
    return [ComparisonRow(label=r.label, erp=r.erp, mega=r.mega) for r in rows]


# --- намиране на черновата ----------------------------------------------------------------------------


def _counterparty_id(session: Session, mega: MegaInvoice) -> int | None:
    if mega.counterparty_id is not None:
        return mega.counterparty_id
    if mega.recipient_eik:
        return session.scalar(select(Counterparty.id).where(Counterparty.eik == mega.recipient_eik))
    return None


def _distance(invoice: Invoice, day: date) -> int:
    if invoice.period_from <= day <= invoice.period_to:
        return 0
    return min(abs((day - invoice.period_from).days), abs((day - invoice.period_to).days))


def find_draft(session: Session, mega: MegaInvoice) -> Invoice | None:
    """Чернова в `shadow` на същия клиент, още несравнена с фактура: периодът покрива `issue_date`,
    иначе най-близкият цикъл."""
    counterparty_id = _counterparty_id(session, mega)
    if counterparty_id is None or mega.issue_date is None:
        return None
    compared = select(InvoiceComparison.invoice_id).where(InvoiceComparison.mega_invoice_id.is_not(None))
    drafts = session.scalars(
        select(Invoice)
        .where(
            Invoice.counterparty_id == counterparty_id,
            Invoice.mode == InvoiceMode.SHADOW.value,
            Invoice.status == InvoiceStatus.DRAFT.value,
            Invoice.id.not_in(compared),
        )
        .order_by(Invoice.period_to, Invoice.id)
    ).all()
    if not drafts:
        return None
    return min(drafts, key=lambda inv: (_distance(inv, mega.issue_date), inv.period_to, inv.id))  # type: ignore[arg-type]


# --- записване ---------------------------------------------------------------------------------------


def _invoice_lines(session: Session, invoice: Invoice) -> list[InvoiceLine]:
    return list(
        session.scalars(
            select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id).order_by(InvoiceLine.line_no)
        )
    )


def _draft_trips(session: Session, invoice: Invoice) -> list[Trip]:
    return list(
        session.scalars(
            select(Trip)
            .join(InvoiceTrip, InvoiceTrip.trip_id == Trip.id)
            .where(InvoiceTrip.invoice_id == invoice.id)
            .order_by(Trip.id)
        )
    )


def mark_trips_invoiced(session: Session, invoice: Invoice, mega_number: str) -> int:
    """Курсовете на черновата получават `invoice_id` (статус „Фактуриран“); идемпотентно."""
    marked = 0
    for trip in _draft_trips(session, invoice):
        if trip.invoice_id is not None:
            continue
        trip.invoice_id = invoice.id
        session.add(
            TripEvent(
                trip_id=trip.id,
                occurred_at=clock.now(),
                kind=TripEventKind.INVOICED.value,
                text=f"Включен във фактура № {mega_number}",
            )
        )
        marked += 1
    session.flush()
    return marked


def _open_cards(session: Session, **where: int) -> list[ActionCard]:
    stmt = select(ActionCard).where(ActionCard.status == CardStatus.OPEN.value)
    for column, value in where.items():
        stmt = stmt.where(getattr(ActionCard, column) == value)
    return list(session.scalars(stmt))


def _supersede_missing_card(session: Session, invoice: Invoice) -> None:
    for card in _open_cards(session, invoice_id=invoice.id):
        if card.kind == CardKind.INVOICE_MISSING.value:
            card.status = CardStatus.SUPERSEDED.value
    session.flush()


def compare_mega_invoice(session: Session, mega: MegaInvoice) -> InvoiceComparison | None:
    """Нова фактура от Мега → сравнение с черновата. `None` — няма на кого/с какво да се сравни."""
    invoice = find_draft(session, mega)
    if invoice is None:
        log.info("mega invoice %s: няма чернова в shadow за сравнение", mega.number)
        return None
    company = session.scalars(select(CompanySettings)).one()
    verdict = judge(
        invoice, _invoice_lines(session, invoice), len(_draft_trips(session, invoice)), mega,
        company.invoice_rounding_tolerance_eur,
    )  # fmt: skip
    # Вече отбелязаното „няма фактура“ се превръща в сравнение, а не в второ.
    comparison = session.scalar(
        select(InvoiceComparison).where(
            InvoiceComparison.invoice_id == invoice.id,
            InvoiceComparison.result == ComparisonResult.MISSING.value,
        )
    )
    if comparison is None:
        comparison = InvoiceComparison(invoice_id=invoice.id)
        session.add(comparison)
    comparison.mega_invoice_id = mega.id
    comparison.result = verdict.result.value
    comparison.diff = verdict.diff
    comparison.diff_kinds = verdict.kinds
    comparison.agent_explanation = verdict.explanation
    session.flush()
    _supersede_missing_card(session, invoice)

    if verdict.result == ComparisonResult.MATCH:
        mark_trips_invoiced(session, invoice, mega.number)
        return comparison
    customer = session.get_one(Counterparty, invoice.counterparty_id)
    payload = InvoiceMismatchPayload(
        comparison_id=comparison.id,
        mega_number=mega.number,
        period_from=invoice.period_from,
        period_to=invoice.period_to,
        rows=_card_rows(rows_from_diff(verdict.diff, verdict.kinds)),
        difference=q2((mega.subtotal or D(0)) - invoice.subtotal),
        explanation=verdict.explanation or "",
    )
    CardService.create_or_get(
        session,
        CardKind.INVOICE_MISMATCH,
        title=f"Разлика във фактура към {customer.name}",
        subtitle=f"Мега № {mega.number}",
        payload=payload,
        dedupe_key=f"invoice_mismatch:{comparison.id}",
        invoice_id=invoice.id,
        invoice_comparison_id=comparison.id,
        counterparty_id=customer.id,
    )
    return comparison


def mega_missing_check(session: Session, today: date) -> list[int]:
    """Задача `invoicing.mega_missing_check`: чернова в `shadow` без сравнение след N работни дни →
    сравнение `missing` и карта `invoice_missing`. Връща id-тата на сравненията."""
    company = session.scalars(select(CompanySettings)).one()
    grace = company.mega_invoice_grace_working_days
    any_comparison = select(InvoiceComparison.invoice_id)
    drafts = session.scalars(
        select(Invoice)
        .where(
            Invoice.mode == InvoiceMode.SHADOW.value,
            Invoice.status == InvoiceStatus.DRAFT.value,
            Invoice.id.not_in(any_comparison),
        )
        .order_by(Invoice.id)
    ).all()
    created: list[int] = []
    for invoice in drafts:
        if add_working_days(invoice.billing_date, grace) > today:
            continue
        customer = session.get_one(Counterparty, invoice.counterparty_id)
        comparison = InvoiceComparison(
            invoice_id=invoice.id, result=ComparisonResult.MISSING.value, diff={}, diff_kinds=[]
        )
        session.add(comparison)
        session.flush()
        CardService.create_or_get(
            session,
            CardKind.INVOICE_MISSING,
            title=f"Няма фактура от Мега за {customer.name}",
            subtitle=f"Период {invoice.period_from:%d.%m} – {invoice.period_to:%d.%m.%Y}",
            payload=InvoiceMissingPayload(
                invoice_id=invoice.id,
                period_from=invoice.period_from,
                period_to=invoice.period_to,
                subtotal=invoice.subtotal,
                billing_date=invoice.billing_date,
            ),
            dedupe_key=f"invoice_missing:{invoice.id}",
            invoice_id=invoice.id,
            invoice_comparison_id=comparison.id,
            counterparty_id=customer.id,
        )
        created.append(comparison.id)
    return created


# --- екран 16 и разрешаване --------------------------------------------------------------------------


def get_comparison(session: Session, comparison_id: int, *, lock: bool = False) -> InvoiceComparison:
    stmt = select(InvoiceComparison).where(InvoiceComparison.id == comparison_id)
    comparison = session.scalar(stmt.with_for_update() if lock else stmt)
    if comparison is None:
        raise DomainError("not_found", "Сравнението не е намерено.", http_status=404)
    return comparison


def comparison_dto(session: Session, comparison: InvoiceComparison) -> ComparisonDTO:
    invoice = session.get_one(Invoice, comparison.invoice_id)
    customer = session.get_one(Counterparty, invoice.counterparty_id)
    mega = session.get(MegaInvoice, comparison.mega_invoice_id) if comparison.mega_invoice_id else None
    return ComparisonDTO(
        id=comparison.id,
        customer_name=customer.name,
        mega_number=mega.number if mega is not None else None,
        rows=rows_from_diff(comparison.diff, comparison.diff_kinds),
        explanation=comparison.agent_explanation,
        resolution=comparison.resolution,
    )


def _accountant_note(session: Session, comparison: InvoiceComparison, invoice: Invoice) -> OutboundEmail:
    company = session.scalars(select(CompanySettings)).one()
    mega = session.get(MegaInvoice, comparison.mega_invoice_id) if comparison.mega_invoice_id else None
    customer = session.get_one(Counterparty, invoice.counterparty_id)
    lines = [f"Разлика във фактура към {customer.name} (период {invoice.period_from} – {invoice.period_to})."]
    if mega is not None:
        lines.append(f"Фактура в Мега: № {mega.number}.")
    lines += [
        f"{r.label}: ERP {r.erp}, Мега {r.mega}"
        for r in rows_from_diff(comparison.diff, comparison.diff_kinds)
    ]
    if comparison.agent_explanation:
        lines.append(comparison.agent_explanation)
    return OutboundService.create_draft(
        session,
        purpose=OutboundPurpose.ACCOUNTANT_NOTE,
        to_addresses=[company.accountant_email] if company.accountant_email else [],
        subject=f"Разлика във фактура към {customer.name}",
        body_text="\n".join(lines),
        counterparty_id=customer.id,
    )


def resolve_comparison(
    session: Session, comparison_id: int, resolution: ComparisonResolution
) -> InvoiceComparison:
    """`mega_correct` — фактурата в Мега е вярна: курсовете се маркират с черновата.
    `notify_accountant` — чернова `accountant_note`, която НЕ се праща автоматично."""
    comparison = get_comparison(session, comparison_id, lock=True)
    if comparison.result != ComparisonResult.MISMATCH.value:
        raise DomainError("invalid_transition", "Само разлика във фактура може да се разреши.", 409)
    if comparison.resolution is not None:
        raise DomainError("invalid_transition", "Сравнението вече е разрешено.", 409)
    invoice = session.get_one(Invoice, comparison.invoice_id)
    card_resolution = CardResolution(resolution.value)
    if resolution == ComparisonResolution.MEGA_CORRECT:
        mega = session.get_one(MegaInvoice, comparison.mega_invoice_id)
        mark_trips_invoiced(session, invoice, mega.number)
    else:
        _accountant_note(session, comparison, invoice)
    comparison.resolution = resolution.value
    comparison.resolved_at = clock.now()
    comparison.resolved_by_id = get_audit_context().actor_id
    for card in _open_cards(session, invoice_comparison_id=comparison.id):
        CardService.resolve(session, card, CardStatus.DONE, card_resolution)
    session.flush()
    return comparison


def _mega_correct_action(session: Session, card: ActionCard, body: dict[str, Any]) -> ActionResult:
    if card.invoice_comparison_id is None:
        raise DomainError("not_found", "Картата няма сравнение.", http_status=404)
    resolve_comparison(session, card.invoice_comparison_id, ComparisonResolution.MEGA_CORRECT)
    return ActionResult("Фактурата от Мега е отбелязана като вярна")


register_action(CardKind.INVOICE_MISMATCH, "mega_correct", _mega_correct_action)
