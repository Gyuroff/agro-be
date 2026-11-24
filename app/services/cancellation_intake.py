"""Агент „откази“ → карта `cancellation` (agro-6p5.6).

`handle` е обработчикът на `EmailKind.CANCELLATION`. `cancellation_parser` само извлича `{date?, plates[],
reason?}`; намирането на курсовете е правило в кода: `planned` курсове на клиента (подателя) по нормализиран
номер и дата ±1 ден (най-близкият ден печели). Без дата — най-близкият курс от днес нататък. Номер без
такъв курс отива в `not_found_plates` (решение на собственика), а картата пак се създава.
`notify_supplier=true` по подразбиране (отворен въпрос 3, както е във Figma).
"""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import run_agent
from app.agents.cancellation_parser import graph as parser_graph
from app.agents.cancellation_parser.prompts import PROMPT_VERSION
from app.agents.cancellation_parser.schemas import CancellationParserOutput
from app.core import clock
from app.domain.plates import format_plate, normalize_plate
from app.models.cards import CardKind
from app.models.mail import InboundEmail, InboundStatus
from app.models.masterdata import Counterparty
from app.models.trips import Trip, TripStatus
from app.schemas.cards import CancellationPayload
from app.services import inbound_mail
from app.services.cards import CardService


def _find(session: Session, customer_id: int, plate_norm: str, day: date | None, today: date) -> list[Trip]:
    """Курсовете на най-близкия ден за номера; без дата — първият ден от днес нататък."""
    query = select(Trip).where(
        Trip.customer_id == customer_id,
        Trip.status == TripStatus.PLANNED.value,
        Trip.plate_norm == plate_norm,
    )
    if day is not None:
        query = query.where(Trip.planned_date.between(day - timedelta(days=1), day + timedelta(days=1)))
    else:
        query = query.where(Trip.planned_date >= today)
    trips = list(session.scalars(query.order_by(Trip.planned_date, Trip.id)))
    if not trips:
        return []
    anchor = day or trips[0].planned_date
    best = min(abs((t.planned_date - anchor).days) for t in trips)
    first = next(t.planned_date for t in trips if abs((t.planned_date - anchor).days) == best)
    return [t for t in trips if t.planned_date == first]


def handle(session: Session, email: InboundEmail) -> InboundStatus:
    if email.counterparty_id is None:  # process() не вика обработчик без контрагент; защита
        return InboundStatus.NEEDS_ACTION
    today = clock.today_sofia()
    outcome = run_agent(
        session,
        parser_graph.AGENT,
        ("inbound_emails", email.id),
        parser_graph.build_graph(),
        {**inbound_mail.email_agent_input(session, email), "today": today.isoformat()},
        PROMPT_VERSION,
    )
    if not outcome.ok or not isinstance(outcome.output, CancellationParserOutput):
        raise RuntimeError(f"cancellation_parser: {outcome.error}")
    parsed = outcome.output

    trips: list[Trip] = []
    not_found: list[str] = []
    seen: set[str] = set()
    for raw in parsed.plates:
        norm = normalize_plate(raw)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        found = _find(session, email.counterparty_id, norm, parsed.date, today)
        if found:
            trips.extend(t for t in found if t not in trips)
        else:
            not_found.append(format_plate(raw))

    customer = session.get(Counterparty, email.counterparty_id)
    assert customer is not None
    supplier_ids = list(dict.fromkeys(t.supplier_id for t in trips if t.supplier_id))
    suppliers = [
        name
        for sid in supplier_ids
        if (name := session.scalar(select(Counterparty.name).where(Counterparty.id == sid)))
    ]
    day = parsed.date or (min(t.planned_date for t in trips) if trips else email.received_at.date())
    payload = CancellationPayload(
        trip_ids=[t.id for t in trips],
        date=day,
        plates=[t.plate_display for t in trips],
        supplier_name=", ".join(suppliers) or "—",
        notify_supplier=True,
        not_found_plates=not_found,
    )
    title = (
        f"Отказани {len(trips)} курса от {customer.name}"
        if trips
        else f"Отказ от {customer.name}: няма курсове"
    )
    subtitle = f"{day:%d.%m.%Y}" + (f" · не са намерени: {', '.join(not_found)}" if not_found else "")
    CardService.create_or_get(
        session,
        CardKind.CANCELLATION,
        title=title,
        subtitle=subtitle,
        payload=payload,
        dedupe_key=f"cancellation:{email.id}",
        counterparty_id=customer.id,
        inbound_email_id=email.id,
    )
    return InboundStatus.NEEDS_ACTION


inbound_mail.KIND_HANDLERS[inbound_mail.EmailKind.CANCELLATION] = handle
