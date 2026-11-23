"""Агенти „справки“ → справка + редове + карта (agro-6p5.5).

`handle` е обработчикът на `EmailKind.REPORT`. `report_parser` само извлича редовете; номерата, тоновете
в кг, съвпадението (`match_lines`) и предложенията за заместване (`suggest_substitutes`) са правила в кода.
`report_matcher` само пише кратко обяснение за вече избраните кандидати (в `agent_runs.output`).
Ред без тонаж остава `matched`, но не се брои в „съвпадат“ и не се отбелязва при `confirm`
(решение на собственика, `bd note agro-6p5.5`).
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.base import run_agent
from app.agents.report_matcher import graph as matcher_graph
from app.agents.report_matcher.prompts import PROMPT_VERSION as MATCHER_PROMPT
from app.agents.report_parser import graph as parser_graph
from app.agents.report_parser.prompts import PROMPT_VERSION as PARSER_PROMPT
from app.agents.report_parser.schemas import ReportLineOut, ReportParserOutput
from app.core import clock
from app.domain.plates import format_plate, is_valid_plate, normalize_plate
from app.models.cards import CardKind
from app.models.mail import EmailAttachment, InboundEmail, InboundStatus
from app.models.masterdata import Counterparty, Product, Warehouse
from app.models.reports import MatchStatus, SupplierReport, SupplierReportLine
from app.models.trips import Trip
from app.schemas.cards import ReportPreviewRow, SupplierReportPayload
from app.services import inbound_mail
from app.services.cards import CardService
from app.services.report_matching import counts_as_matched, match_lines, suggest_substitutes
from app.services.report_views import substitutes

KG_THRESHOLD = Decimal("1000")  # камион не вози 1000 т: толкова голяма стойност е в килограми
PREVIEW_ROWS = 3


def _warehouse_by_hint(session: Session, supplier_id: int, hint: str | None) -> int | None:
    """„Място на товарене“ от файла → склад на доставчика по име (само ако е еднозначно)."""
    if not hint:
        return None
    h = hint.casefold().strip()
    found = [
        w.id
        for w in session.scalars(
            select(Warehouse).where(Warehouse.counterparty_id == supplier_id, Warehouse.is_active.is_(True))
        )
        if h and (h in w.name.casefold() or w.name.casefold() in h)
    ]
    return found[0] if len(found) == 1 else None


def _line(session: Session, report: SupplierReport, row_no: int, item: ReportLineOut) -> SupplierReportLine:
    uncertain = dict.fromkeys(item.uncertain_fields)
    qty = item.qty_t
    if qty is not None and qty >= KG_THRESHOLD:
        qty = qty / 1000
        uncertain["qty"] = None
    if qty is None:
        uncertain["qty"] = None
    if not is_valid_plate(item.plate_raw):
        uncertain["plate"] = None
    if item.load_date is None:
        uncertain["date"] = None
    return SupplierReportLine(
        report_id=report.id,
        row_no=item.source_row or row_no,
        load_date=item.load_date,
        plate_raw=item.plate_raw or None,
        plate_norm=normalize_plate(item.plate_raw) if item.plate_raw else None,
        driver_name=item.driver_name,
        qty_t=qty.quantize(Decimal("0.001")) if qty is not None else None,
        warehouse_id=_warehouse_by_hint(session, report.supplier_id, item.location_raw),
        match_status=MatchStatus.UNPLANNED.value,
        uncertain_fields=list(uncertain),
    )


def _explain(session: Session, report: SupplierReport, lines: list[SupplierReportLine]) -> None:
    """Обяснения от `report_matcher` за предложенията; провал на агента не пречи на справката."""
    by_id = {line.id: line for line in lines}
    candidates = []
    for u in lines:
        m = by_id.get(u.substitute_for_line_id or 0)
        if u.match_status != MatchStatus.UNPLANNED.value or m is None:
            continue
        candidates.append(
            {
                "unplanned_line_id": u.id,
                "new": {
                    "plate": format_plate(u.plate_raw or ""),
                    "date": str(u.load_date),
                    "qty_t": str(u.qty_t),
                },
                "old": {
                    "plate": format_plate(m.plate_raw or ""),
                    "date": str(m.load_date),
                    "planned_qty_t": str(m.qty_t),
                },
            }
        )
    if not candidates:
        return
    run_agent(
        session,
        matcher_graph.AGENT,
        ("supplier_reports", report.id),
        matcher_graph.build_graph(),
        {"candidates": candidates},
        MATCHER_PROMPT,
    )


def _payload(
    session: Session, report: SupplierReport, lines: list[SupplierReportLine]
) -> SupplierReportPayload:
    email = session.get(InboundEmail, report.inbound_email_id)
    assert email is not None
    matched = [line for line in lines if counts_as_matched(line)]
    preview: list[ReportPreviewRow] = []
    customer_ids: list[int] = []
    for line in lines:
        trip = session.get(Trip, line.trip_id) if line.trip_id else None
        if trip is not None and trip.customer_id is not None and trip.customer_id not in customer_ids:
            customer_ids.append(trip.customer_id)
        if counts_as_matched(line) and trip is not None and len(preview) < PREVIEW_ROWS:
            product = session.get(Product, trip.product_id)
            assert line.qty_t is not None
            preview.append(
                ReportPreviewRow(
                    plate=trip.plate_display, product=product.name if product else "—", qty_t=line.qty_t
                )
            )
    names = [cp.name for cid in customer_ids if (cp := session.get(Counterparty, cid)) is not None]
    return SupplierReportPayload(
        report_id=report.id,
        inbound_email_id=email.id,
        received_at=email.received_at,
        total_rows=report.total_rows,
        total_qty_t=report.total_qty_t,
        matched=len(matched),
        unplanned=sum(1 for ln in lines if ln.match_status == MatchStatus.UNPLANNED.value),
        missing=sum(1 for ln in lines if ln.match_status == MatchStatus.MISSING.value),
        preview=preview,
        substitutes=substitutes(session, lines),
        customer_names=names,
    )


def handle(session: Session, email: InboundEmail) -> InboundStatus:
    if email.counterparty_id is None:  # process() не вика обработчик без контрагент; защита
        return InboundStatus.NEEDS_ACTION
    if session.scalar(select(SupplierReport.id).where(SupplierReport.inbound_email_id == email.id)):
        return InboundStatus.NEEDS_ACTION  # повторно пускане: справката вече е създадена

    agent_input = {
        **inbound_mail.email_agent_input(session, email, row_numbers=True, windowed=False),
        "today": clock.today_sofia().isoformat(),
    }
    outcome = run_agent(
        session,
        parser_graph.AGENT,
        ("inbound_emails", email.id),
        parser_graph.build_graph(),
        agent_input,
        PARSER_PROMPT,
    )
    if not outcome.ok or not isinstance(outcome.output, ReportParserOutput):
        raise RuntimeError(f"report_parser: {outcome.error}")

    supplier = session.get(Counterparty, email.counterparty_id)
    assert supplier is not None
    report = SupplierReport(
        inbound_email_id=email.id,
        supplier_id=supplier.id,
        attachment_id=session.scalar(
            select(EmailAttachment.id)
            .where(
                EmailAttachment.inbound_email_id == email.id,
                func.lower(EmailAttachment.filename).like("%.xlsx"),
            )
            .order_by(EmailAttachment.id)
            .limit(1)
        ),
        agent_run_id=outcome.run_id,
    )
    session.add(report)
    session.flush()
    for no, item in enumerate(outcome.output.lines, start=1):
        session.add(_line(session, report, no, item))
    session.flush()
    report.total_rows = len(outcome.output.lines)
    report.total_qty_t = sum((i.qty_t or Decimal("0") for i in outcome.output.lines), Decimal("0"))

    lines = match_lines(session, report)
    suggest_substitutes(session, report)
    _explain(session, report, lines)

    payload = _payload(session, report, lines)
    days: list[date] = [ln.load_date for ln in lines if ln.load_date is not None]
    subtitle = f"{report.total_rows} курса · {report.total_qty_t:.2f} т" + (
        f" · {min(days):%d.%m}–{max(days):%d.%m}" if days else ""
    )
    CardService.create_or_get(
        session,
        CardKind.SUPPLIER_REPORT,
        title=f"Справка от {supplier.name}",
        subtitle=subtitle,
        payload=payload,
        dedupe_key=f"supplier_report:{email.id}",
        counterparty_id=supplier.id,
        inbound_email_id=email.id,
        supplier_report_id=report.id,
    )
    return InboundStatus.NEEDS_ACTION


inbound_mail.KIND_HANDLERS[inbound_mail.EmailKind.REPORT] = handle
