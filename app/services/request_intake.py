"""Агент „заявки“ → заявка + карта (agro-6p5.4).

`handle` е обработчикът на `EmailKind.REQUEST` в `inbound_mail.KIND_HANDLERS`. Агентът само извлича
редовете; номерата, датите, количествата и изборът на договор са правила тук (architecture §7).
Писмото към доставчика НЕ се създава тук: `RequestService.send` го прави при „Изпрати“ (br9.6),
та отхвърлена заявка да не изгаря АИТ номер.
"""

import logging
from collections import OrderedDict
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import run_agent
from app.agents.request_parser.graph import AGENT, build_graph
from app.agents.request_parser.prompts import PROMPT_VERSION
from app.agents.request_parser.schemas import ParsedLine, RequestParserOutput
from app.core import clock
from app.domain.plates import format_plate, is_valid_plate, normalize_plate
from app.models.cards import CardKind
from app.models.company import CompanySettings
from app.models.contracts import Contract, ContractSide, ContractStatus
from app.models.mail import EmailKind, InboundEmail, InboundStatus
from app.models.masterdata import Contact, Counterparty, Product, Warehouse
from app.models.trips import ClientRequest, ClientRequestLine, RequestStatus
from app.schemas.cards import ClientRequestLineView, ClientRequestPayload
from app.services import inbound_mail
from app.services.cards import CardService
from app.services.requests import RequestService

log = logging.getLogger(__name__)

NO_CONTRACT_WARNING = "избери договор"
KG_THRESHOLD = Decimal("1000")  # камион не вози 1000 т: толкова голяма стойност е в килограми


def _fmt_day(day: date) -> str:
    return day.strftime("%d.%m")


def _hint_matches(hint: str | None, name: str | None) -> bool:
    if not hint or not name:
        return False
    a, b = hint.casefold().strip(), name.casefold().strip()
    return bool(a) and (a in b or b in a)


def pick_contract(
    session: Session, customer_id: int, product_hint: str | None, warehouse_hint: str | None
) -> Contract | None:
    """Един активен договор за продажба → него; няколко → по стока/склад на supply договора; иначе None."""
    candidates = list(
        session.scalars(
            select(Contract)
            .where(
                Contract.counterparty_id == customer_id,
                Contract.side == ContractSide.SALE.value,
                Contract.status == ContractStatus.ACTIVE.value,
            )
            .order_by(Contract.id)
        )
    )
    if len(candidates) <= 1:
        return candidates[0] if candidates else None

    def product_name(c: Contract) -> str | None:
        return session.scalar(select(Product.name).where(Product.id == c.product_id))

    def warehouse_name(c: Contract) -> str | None:
        supply = session.get(Contract, c.supply_contract_id) if c.supply_contract_id else None
        if supply is None or supply.warehouse_id is None:
            return None
        return session.scalar(select(Warehouse.name).where(Warehouse.id == supply.warehouse_id))

    for hint, name_of in ((product_hint, product_name), (warehouse_hint, warehouse_name)):
        if hint:
            narrowed = [c for c in candidates if _hint_matches(hint, name_of(c))]
            if narrowed:  # подсказка, която не съвпада с нищо, не стеснява (и не изключва всичко)
                candidates = narrowed
    return candidates[0] if len(candidates) == 1 else None


def split_plates(plate_raw: str, trailer: str | None) -> tuple[str, str | None]:
    """„СВ7246НХ/ СВ5874ЕА“ → (влекач, ремарке). Само „/“: в реалните файлове така се пише двойката."""
    if "/" in plate_raw:
        head, _, tail = plate_raw.partition("/")
        return head.strip(), (trailer or tail.strip() or None)
    return plate_raw.strip(), trailer


def _build_lines(parsed: list[ParsedLine], default_qty: Decimal, today: date) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in parsed:
        plate_raw, trailer = split_plates(item.plate_raw, item.trailer_plate)
        uncertain: OrderedDict[str, None] = OrderedDict.fromkeys(item.uncertain_fields)
        notes = [item.uncertain_note] if item.uncertain_note else []
        if not is_valid_plate(plate_raw):
            uncertain["plate"] = None
            notes.append(f"невалиден номер „{plate_raw}“" if plate_raw else "няма номер")
        if item.date < today:
            uncertain["date"] = None
            notes.append(f"датата {item.date.strftime('%d.%m.%Y')} е в миналото")
        qty = item.qty_t
        if qty is not None and qty >= KG_THRESHOLD:
            qty = qty / 1000
            uncertain["qty"] = None
            notes.append(f"количеството {item.qty_t} е прието за кг → {qty:.3f} т")
        rows.append(
            {
                "planned_date": item.date,
                "plate_raw": plate_raw,
                "plate_norm": normalize_plate(plate_raw),
                "trailer_plate": trailer,
                "driver_name": item.driver_name,
                "driver_phone": item.driver_phone,
                "planned_qty_t": qty if qty is not None else default_qty,
                "uncertain_fields": list(uncertain),
                "uncertain_note": "; ".join(notes) or None,
            }
        )
    return rows


def _agent_notes(
    customer: Counterparty,
    contract: Contract | None,
    output: RequestParserOutput,
    start: date,
    end: date,
    rows: list[dict[str, Any]],
    reason_contract: str,
) -> list[dict[str, str]]:
    total = sum((r["planned_qty_t"] for r in rows), Decimal("0"))
    unsure = [r for r in rows if r["uncertain_fields"]]
    notes = [
        {"label": "Вид", "value": "Заявка за курсове", "why": "по текста и прикачените файлове"},
        {"label": "Клиент", "value": customer.name, "why": "подателят е съпоставен с клиент"},
        {
            "label": "Договор",
            "value": contract.number if contract is not None else "не е избран",
            "why": reason_contract,
        },
        {
            "label": "Период",
            "value": f"{start.strftime('%d.%m.%Y')}–{end.strftime('%d.%m.%Y')}",
            "why": "от писмото"
            if output.period_from and output.period_to
            else "по най-ранния и най-късния ред",
        },
        {
            "label": "Камиони",
            "value": f"{len(rows)} камиона, {total:.3f} т",
            "why": "количеството без посочена стойност е по договора",
        },
    ]
    for r in unsure:
        notes.append(
            {
                "label": f"Несигурно: {format_plate(r['plate_raw'])}",
                "value": ", ".join(r["uncertain_fields"]),
                "why": r["uncertain_note"] or "агентът не е сигурен",
            }
        )
    return notes


def handle(session: Session, email: InboundEmail) -> InboundStatus:
    if email.counterparty_id is None:  # process() не вика обработчик без клиент; защита
        return InboundStatus.NEEDS_ACTION
    if session.scalar(select(ClientRequest.id).where(ClientRequest.inbound_email_id == email.id)):
        return InboundStatus.NEEDS_ACTION  # повторно пускане: заявката вече е създадена

    today = clock.today_sofia()
    agent_input = {**inbound_mail.email_agent_input(session, email), "today": today.isoformat()}
    outcome = run_agent(
        session, AGENT, ("inbound_emails", email.id), build_graph(), agent_input, PROMPT_VERSION
    )
    if not outcome.ok or not isinstance(outcome.output, RequestParserOutput):
        raise RuntimeError(f"request_parser: {outcome.error}")
    output = outcome.output

    customer = session.get(Counterparty, email.counterparty_id)
    assert customer is not None
    contract = pick_contract(session, customer.id, output.product_hint, output.warehouse_hint)
    default_qty = (
        contract.planned_truck_qty_t
        if contract is not None
        else session.scalar(select(CompanySettings.default_planned_truck_qty_t))
    ) or Decimal("25.200")
    rows = _build_lines(output.lines, default_qty, today)

    days = [r["planned_date"] for r in rows]
    start, end = output.period_from or min(days), output.period_to or max(days)
    if contract is None:
        reason = "няма еднозначен активен договор — изберете ръчно"
    elif output.product_hint or output.warehouse_hint:
        reason = "по подсказка за стока/склад"
    else:
        reason = "единственият активен договор на клиента"

    request = ClientRequest(
        inbound_email_id=email.id,
        sale_contract_id=contract.id if contract is not None else None,
        period_from=start,
        period_to=end,
        status=RequestStatus.DRAFT.value,
        agent_run_id=outcome.run_id,
        agent_notes=_agent_notes(customer, contract, output, start, end, rows, reason),
    )
    session.add(request)
    session.flush()
    for no, r in enumerate(rows, start=1):
        session.add(ClientRequestLine(request_id=request.id, line_no=no, **r))
    session.flush()

    lines = RequestService.lines(session, request)
    purchase = RequestService._purchase(session, request)
    total = sum((line.planned_qty_t for line in lines), Decimal("0"))
    payload = ClientRequestPayload(
        request_id=request.id,
        inbound_email_id=email.id,
        to=RequestService._order_address(session, purchase) or "",
        subject=RequestService.default_subject(session, request, lines),
        greeting=RequestService.default_greeting(session, request, lines),
        lines=[
            ClientRequestLineView(
                date=line.planned_date,
                plate=format_plate(line.plate_raw),
                driver=line.driver_name,
                qty_t=line.planned_qty_t,
                uncertain=bool(line.uncertain_fields),
            )
            for line in lines
        ],
        total_trucks=len(lines),
        total_qty_t=total,
        has_uncertain=any(line.uncertain_fields for line in lines),
    )
    product = session.get(Product, contract.product_id) if contract is not None else None
    parts = [
        f"{len(lines)} камиона",
        f"{_fmt_day(start)}–{_fmt_day(end)}",
        product.name if product is not None else (output.product_hint or ""),
        NO_CONTRACT_WARNING if contract is None else "",
    ]
    subtitle = " · ".join(p for p in parts if p)
    contact = session.get(Contact, email.contact_id) if email.contact_id else None
    CardService.create_or_get(
        session,
        CardKind.CLIENT_REQUEST,
        title=f"Нова заявка от {customer.name}",
        subtitle=subtitle,
        payload=payload,
        dedupe_key=f"client_request:{email.id}",
        call_phone=(contact.phone if contact is not None and contact.phone else customer.phone),
        counterparty_id=customer.id,
        inbound_email_id=email.id,
        client_request_id=request.id,
        contract_id=contract.id if contract is not None else None,
    )
    return InboundStatus.NEEDS_ACTION


inbound_mail.KIND_HANDLERS[EmailKind.REQUEST] = handle
