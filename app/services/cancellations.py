"""Карта „Отказани курсове“ (agro-br9.8): `confirm {notify_supplier}` и `keep` (docs/api.md §2).

`confirm`: само `planned` курсовете → `cancelled` (source=email); останалите се пропускат и се казват в
snackbar. При `notify_supplier` — писмо `supplier_cancellation` до `order_contact` на покупния договор,
`queued` БЕЗ прозорец за „Отмени“ (изпращането е веднага). Отказът е вътрешен и е истински и в съветен
режим; там писмото само се записва като предложение (`ActionGateway`).
"""

from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.errors import DomainError
from app.mail import smtp
from app.models.cards import ActionCard, CardKind, CardResolution, CardStatus
from app.models.company import CompanySettings
from app.models.contracts import Contract
from app.models.mail import OutboundPurpose
from app.models.masterdata import Contact, Counterparty
from app.models.trips import Trip, TripStatus
from app.schemas.cards import CancellationPayload
from app.services import outbound as outbound_service
from app.services.action_gateway import ProposalSpec, is_advisory, propose
from app.services.cards import ActionResult, CardService, register_action
from app.services.contract_alerts import check_over
from app.services.outbound import OutboundService
from app.services.trips import CancelSource, TripService

SUBJECT = "Отказани курсове {date} — {customer}"


class _ConfirmBody(BaseModel):
    notify_supplier: bool | None = None


def _payload(card: ActionCard) -> CancellationPayload:
    return CancellationPayload.model_validate(card.payload)


def _body(card: ActionCard, raw: dict[str, Any]) -> bool:
    try:
        data = _ConfirmBody.model_validate(raw)
    except ValidationError as exc:
        fields = {".".join(map(str, e["loc"])): e["msg"] for e in exc.errors()}
        raise DomainError("validation_error", "Невалидни данни.", http_status=400, fields=fields) from exc
    return data.notify_supplier if data.notify_supplier is not None else _payload(card).notify_supplier


def _trips(session: Session, trip_ids: list[int]) -> list[Trip]:
    rows = session.scalars(select(Trip).where(Trip.id.in_(trip_ids)).order_by(Trip.id).with_for_update())
    return list(rows)


def _order_address(session: Session, trips: list[Trip]) -> str | None:
    for trip in trips:
        purchase = session.get(Contract, trip.purchase_contract_id) if trip.purchase_contract_id else None
        contact = (
            session.get(Contact, purchase.order_contact_id)
            if purchase and purchase.order_contact_id
            else None
        )
        if contact is not None and contact.email:
            return contact.email
    return None


def _compose(session: Session, card: ActionCard, trips: list[Trip]) -> tuple[str, str, str, int | None]:
    """`(до, тема, текст, доставчик)` — един код за истинското писмо и за предложението."""
    data = _payload(card)
    to = _order_address(session, trips)
    if to is None:
        raise DomainError(
            "validation_error",
            "Покупният договор няма контакт за поръчки с имейл.",
            http_status=400,
            fields={"notify_supplier": "няма имейл на доставчика"},
        )
    customer_id = next((t.customer_id for t in trips if t.customer_id), None)
    customer = session.scalar(select(Counterparty.name).where(Counterparty.id == customer_id)) or "—"
    day = data.date.strftime("%d.%m.%Y")
    plates = "\n".join(f"- {t.plate_display}" for t in trips)
    text = f"Здравейте,\n\nОтказваме следните курсове за {day} ({customer}):\n{plates}"
    supplier_id = next((t.supplier_id for t in trips if t.supplier_id), None)
    return to, SUBJECT.format(date=day, customer=customer), text, supplier_id


def confirm(session: Session, card: ActionCard, body: dict[str, Any]) -> ActionResult:
    notify = _body(card, body)
    trips = _trips(session, _payload(card).trip_ids)
    to, subject, text, supplier_id = _compose(session, card, trips) if notify else ("", "", "", None)

    service = TripService(session)
    cancelled = [t for t in trips if t.status == TripStatus.PLANNED.value]
    skipped = len(trips) - len(cancelled)
    for trip in cancelled:
        service.cancel(trip, source=CancelSource.EMAIL)
    for contract_id in {t.sale_contract_id for t in cancelled if t.sale_contract_id}:
        contract = session.get(Contract, contract_id)
        if contract is not None:
            check_over(session, contract)

    letter = False
    advisory_note = False
    if notify and cancelled:
        if is_advisory(session):
            spec = ProposalSpec(
                kind="cancel_supplier",
                title="Бих уведомила доставчика за отказаните курсове",
                payload={
                    "date": _payload(card).date.isoformat(),
                    "plates": [t.plate_display for t in cancelled],
                    "to": to,
                    "subject": smtp.subject_with_reference(subject, "АИТ-…"),
                    "body_text": smtp.render_body(
                        text, (session.get(CompanySettings, 1) or CompanySettings()).email_signature or ""
                    ),
                },
                deal_ref=f"action_cards:{card.id}",
            )
            propose(session, card, spec)
            advisory_note = True
        else:
            outbound = OutboundService.create_draft(
                session,
                purpose=OutboundPurpose.SUPPLIER_CANCELLATION,
                to_addresses=[to],
                subject=subject,
                body_text=text,
                counterparty_id=supplier_id,
            )
            OutboundService.queue(session, outbound)
            outbound.send_after = clock.now()  # без прозорец за „Отмени“
            card.outbound_email_id = outbound.id
            outbound_service.schedule_send(session, outbound)
            letter = True

    CardService.resolve(session, card, CardStatus.DONE, CardResolution.CONFIRMED)
    parts = [f"Отказани курсове: {len(cancelled)}"]
    if skipped:
        parts.append(
            "1 курс вече е изпълнен и не е отказан"
            if skipped == 1
            else f"{skipped} курса вече са изпълнени и не са отказани"
        )
    if letter:
        parts.append("писмото до доставчика е изпратено")
    if advisory_note:
        parts.append("писмото до доставчика не е изпратено (съветен режим)")
    return ActionResult("; ".join(parts))


def keep(session: Session, card: ActionCard, body: dict[str, Any]) -> ActionResult:
    CardService.resolve(session, card, CardStatus.DISMISSED, CardResolution.KEPT)
    return ActionResult("Курсовете остават")


def register() -> None:
    register_action(CardKind.CANCELLATION, "confirm", confirm)
    register_action(CardKind.CANCELLATION, "keep", keep)


register()
