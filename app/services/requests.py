"""RequestService — заявки от клиенти: редакция, „Изпрати“, „Отмени“, `finalize` (docs/api.md §2–§3).

- Темата и приветствието на писмото се пазят в `payload` на картата `client_request` (`subject`, `greeting`):
  таблицата `client_requests` няма такива колони.
- `send` НЕ създава курсове: слага заявката и писмото в `queued`; курсовете се създават от `finalize`,
  която `mail.send_outbound` вика в същата транзакция преди SMTP (регистрира се през `register_before_send`).
- `undo` връща писмото и заявката в `draft`, а картата в `open`.
- TODO(T4.3): след `finalize` трябва да се преизчисли `contract_over` — помощникът още не съществува.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.core.errors import DomainError
from app.domain.plates import format_plate, is_valid_plate, normalize_plate
from app.mail import smtp
from app.models.cards import ActionCard, CardKind, CardResolution, CardStatus
from app.models.contracts import Contract
from app.models.mail import OutboundEmail, OutboundPurpose, OutboundStatus
from app.models.masterdata import Contact, Counterparty
from app.models.trips import ClientRequest, ClientRequestLine, RequestStatus
from app.schemas.cards import ClientRequestLineView, ClientRequestPayload
from app.schemas.requests import LineBody, PatchRequestBody, RequestDTO, RequestLineDTO
from app.services import outbound as outbound_service
from app.services.cards import ActionResult, CardService, register_action
from app.services.outbound import OutboundService
from app.services.trips import PlannedTripData, TripService

UNDO_SALT = "request-undo"
UNDO_MAX_AGE_SECONDS = 24 * 3600
_FIELD_LABELS = {
    "driver_name": "шофьора",
    "plate": "номера",
    "planned_qty_t": "количеството",
    "planned_date": "датата",
}


class UndoExpired(DomainError):
    def __init__(self) -> None:
        super().__init__("undo_expired", "Писмото вече е изпратено.", http_status=410)


class InvalidPlate(DomainError):
    def __init__(self) -> None:
        msg = "Невалиден регистрационен номер."
        super().__init__("validation_error", msg, http_status=400, fields={"plate": msg})


def _not_draft(request: ClientRequest) -> DomainError:
    return DomainError(
        "invalid_transition",
        f"Заявка със статус „{request.status}“ не може да се променя.",
        http_status=409,
    )


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().jwt_secret.get_secret_value(), salt=UNDO_SALT)


def make_undo_token(outbound_id: int) -> str:
    return _serializer().dumps({"outbound_email_id": outbound_id})


def _fmt_day(day: date) -> str:
    return day.strftime("%d.%m")


def _fmt_qty(value: Decimal) -> str:
    return f"{value:.3f}"


class RequestService:
    # ---------- четене ----------

    @staticmethod
    def get(session: Session, request_id: int, *, lock: bool = False) -> ClientRequest:
        stmt = select(ClientRequest).where(ClientRequest.id == request_id)
        request = session.scalar(stmt.with_for_update() if lock else stmt)
        if request is None:
            raise DomainError("not_found", "Заявката не е намерена.", http_status=404)
        return request

    @staticmethod
    def lines(
        session: Session, request: ClientRequest, *, include_removed: bool = False
    ) -> list[ClientRequestLine]:
        stmt = select(ClientRequestLine).where(ClientRequestLine.request_id == request.id)
        if not include_removed:
            stmt = stmt.where(ClientRequestLine.is_removed.is_(False))
        return list(session.scalars(stmt.order_by(ClientRequestLine.line_no)))

    @staticmethod
    def card(session: Session, request: ClientRequest) -> ActionCard | None:
        return session.scalar(
            select(ActionCard)
            .where(
                ActionCard.client_request_id == request.id,
                ActionCard.kind == CardKind.CLIENT_REQUEST.value,
            )
            .order_by(ActionCard.id.desc())
        )

    @staticmethod
    def _purchase(session: Session, request: ClientRequest) -> Contract | None:
        sale = session.get(Contract, request.sale_contract_id) if request.sale_contract_id else None
        if sale is None or sale.supply_contract_id is None:
            return None
        return session.get(Contract, sale.supply_contract_id)

    @staticmethod
    def _order_address(session: Session, purchase: Contract | None) -> str | None:
        if purchase is None or purchase.order_contact_id is None:
            return None
        contact = session.get(Contact, purchase.order_contact_id)
        return contact.email if contact is not None else None

    @staticmethod
    def _customer_name(session: Session, request: ClientRequest) -> str:
        sale = session.get(Contract, request.sale_contract_id) if request.sale_contract_id else None
        cp = session.get(Counterparty, sale.counterparty_id) if sale is not None else None
        return cp.name if cp is not None else "—"

    @staticmethod
    def _period(request: ClientRequest, lines: list[ClientRequestLine]) -> tuple[date, date] | None:
        days = [line.planned_date for line in lines]
        start = request.period_from or (min(days) if days else None)
        end = request.period_to or (max(days) if days else None)
        return (start, end) if start and end else None

    @staticmethod
    def default_subject(session: Session, request: ClientRequest, lines: list[ClientRequestLine]) -> str:
        period = RequestService._period(request, lines)
        span = f"{_fmt_day(period[0])}–{_fmt_day(period[1])}" if period else ""
        return f"Курсове {span} — {RequestService._customer_name(session, request)}".replace("  ", " ")

    @staticmethod
    def default_greeting(session: Session, request: ClientRequest, lines: list[ClientRequestLine]) -> str:
        period = RequestService._period(request, lines)
        span = f" за периода {_fmt_day(period[0])}–{_fmt_day(period[1])}" if period else ""
        name = RequestService._customer_name(session, request)
        return f"Здравейте,\nИзпращаме ви курсовете на {name}{span}:"

    @staticmethod
    def texts(session: Session, request: ClientRequest, lines: list[ClientRequestLine]) -> tuple[str, str]:
        """`(subject, greeting)`: редактираното в картата, иначе по подразбиране."""
        card = RequestService.card(session, request)
        payload: dict[str, Any] = card.payload if card is not None else {}
        return (
            payload.get("subject") or RequestService.default_subject(session, request, lines),
            payload.get("greeting") or RequestService.default_greeting(session, request, lines),
        )

    @staticmethod
    def to_dto(session: Session, request: ClientRequest) -> RequestDTO:
        lines = RequestService.lines(session, request)
        subject, greeting = RequestService.texts(session, request, lines)
        sale = session.get(Contract, request.sale_contract_id) if request.sale_contract_id else None
        purchase = RequestService._purchase(session, request)
        supplier = session.get(Counterparty, purchase.counterparty_id) if purchase is not None else None
        warnings = [
            f"Агентът не е сигурен за {_FIELD_LABELS.get(f, f)} на {format_plate(line.plate_raw)}"
            + (f" — {line.uncertain_note}" if line.uncertain_note else "")
            for line in lines
            for f in line.uncertain_fields
        ]
        return RequestDTO(
            id=request.id,
            status=request.status,
            to=RequestService._order_address(session, purchase),
            supplier_name=supplier.name if supplier is not None else None,
            subject=subject,
            body_intro=greeting,
            sale_contract=(
                {"id": sale.id, "number": sale.number} if sale is not None else None  # type: ignore[arg-type]
            ),
            lines=[
                RequestLineDTO(
                    id=line.id,
                    line_no=line.line_no,
                    planned_date=line.planned_date,
                    plate=format_plate(line.plate_raw),
                    driver_name=line.driver_name,
                    planned_qty_t=line.planned_qty_t,
                    uncertain_fields=line.uncertain_fields,
                    uncertain_note=line.uncertain_note,
                )
                for line in lines
            ],
            warnings=warnings,
            version=request.version,
        )

    # ---------- редакция ----------

    @staticmethod
    def _require_draft(request: ClientRequest) -> None:
        if request.status != RequestStatus.DRAFT.value:
            raise _not_draft(request)

    @staticmethod
    def _touch(session: Session, request: ClientRequest) -> None:
        """Промяна по редовете/картата = нова версия на заявката (оптимистично заключване)."""
        request.updated_at = clock.now()
        session.flush()
        RequestService._refresh_card(session, request)

    @staticmethod
    def _refresh_card(
        session: Session, request: ClientRequest, *, subject: str | None = None, greeting: str | None = None
    ) -> None:
        card = RequestService.card(session, request)
        if card is None or card.status != CardStatus.OPEN.value:
            return
        lines = RequestService.lines(session, request)
        old_subject, old_greeting = RequestService.texts(session, request, lines)
        purchase = RequestService._purchase(session, request)
        payload = ClientRequestPayload(
            request_id=request.id,
            inbound_email_id=request.inbound_email_id,
            to=RequestService._order_address(session, purchase) or card.payload.get("to", ""),
            subject=subject or old_subject,
            greeting=greeting if greeting is not None else old_greeting,
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
            total_qty_t=sum((line.planned_qty_t for line in lines), Decimal("0")),
            has_uncertain=any(line.uncertain_fields for line in lines),
        )
        card.payload = payload.model_dump(mode="json")
        session.flush()

    @staticmethod
    def update(session: Session, request: ClientRequest, body: PatchRequestBody) -> ClientRequest:
        RequestService._require_draft(request)
        if request.version != body.version:
            raise DomainError("version_conflict", "Заявката е променена от друг.", http_status=409)
        request.updated_at = clock.now()
        session.flush()
        RequestService._refresh_card(session, request, subject=body.subject, greeting=body.body_intro)
        return request

    @staticmethod
    def _apply(line: ClientRequestLine, body: LineBody) -> None:
        if not is_valid_plate(body.plate):
            raise InvalidPlate
        changed = {
            "planned_date": line.planned_date != body.planned_date,
            "plate": line.plate_norm != normalize_plate(body.plate),
            "driver_name": line.driver_name != body.driver_name,
            "planned_qty_t": line.planned_qty_t != body.planned_qty_t,
        }
        line.planned_date = body.planned_date
        line.plate_raw = body.plate
        line.plate_norm = normalize_plate(body.plate)
        line.driver_name = body.driver_name
        line.planned_qty_t = body.planned_qty_t
        # Човекът е поправил полето — агентът вече не е „несигурен“ за него.
        line.uncertain_fields = [f for f in line.uncertain_fields if not changed.get(f, False)]
        if not line.uncertain_fields:
            line.uncertain_note = None

    @staticmethod
    def _line(session: Session, request: ClientRequest, line_id: int) -> ClientRequestLine:
        line = session.get(ClientRequestLine, line_id)
        if line is None or line.request_id != request.id or line.is_removed:
            raise DomainError("not_found", "Редът не е намерен.", http_status=404)
        return line

    @staticmethod
    def update_line(
        session: Session, request: ClientRequest, line_id: int, body: LineBody
    ) -> ClientRequestLine:
        RequestService._require_draft(request)
        line = RequestService._line(session, request, line_id)
        RequestService._apply(line, body)
        RequestService._touch(session, request)
        return line

    @staticmethod
    def add_line(session: Session, request: ClientRequest, body: LineBody) -> ClientRequestLine:
        RequestService._require_draft(request)
        if not is_valid_plate(body.plate):
            raise InvalidPlate
        last = max(
            (line.line_no for line in RequestService.lines(session, request, include_removed=True)), default=0
        )
        line = ClientRequestLine(
            request_id=request.id,
            line_no=last + 1,
            planned_date=body.planned_date,
            plate_raw=body.plate,
            plate_norm=normalize_plate(body.plate),
            driver_name=body.driver_name,
            planned_qty_t=body.planned_qty_t,
        )
        session.add(line)
        RequestService._touch(session, request)
        return line

    @staticmethod
    def remove_line(session: Session, request: ClientRequest, line_id: int) -> None:
        RequestService._require_draft(request)
        RequestService._line(session, request, line_id).is_removed = True
        RequestService._touch(session, request)

    # ---------- действия по картата ----------

    @staticmethod
    def body_text(
        session: Session, request: ClientRequest, lines: list[ClientRequestLine], greeting: str
    ) -> str:
        """Поздрав + таблица (Дата, Камион, Шофьор, К-во) + общо; подписът го добавя `smtp.build_message`."""
        rows = ["Дата | Камион | Шофьор | К-во (т)"]
        rows += [
            f"{line.planned_date.strftime('%d.%m.%Y')} | {format_plate(line.plate_raw)} | "
            f"{line.driver_name or '—'} | {_fmt_qty(line.planned_qty_t)}"
            for line in lines
        ]
        total = sum((line.planned_qty_t for line in lines), Decimal("0"))
        return (
            f"{greeting.rstrip()}\n\n"
            + "\n".join(rows)
            + f"\n\nОбщо: {len(lines)} камиона, {_fmt_qty(total)} т"
        )

    @staticmethod
    def send(session: Session, card: ActionCard, body: dict[str, Any]) -> ActionResult:
        if card.client_request_id is None:
            raise DomainError("not_found", "Картата няма заявка.", http_status=404)
        request = RequestService.get(session, card.client_request_id, lock=True)
        RequestService._require_draft(request)
        lines = RequestService.lines(session, request)
        purchase = RequestService._purchase(session, request)
        if request.sale_contract_id is None or purchase is None:
            raise DomainError("validation_error", "Изберете договор за заявката.", http_status=400)
        to = RequestService._order_address(session, purchase)
        if to is None:
            raise DomainError("validation_error", "Доставчикът няма адрес за поръчки.", http_status=400)
        if not lines:
            raise DomainError("validation_error", "Заявката няма камиони.", http_status=400)

        subject, greeting = RequestService.texts(session, request, lines)
        text = RequestService.body_text(session, request, lines, greeting)
        outbound = (
            session.get(OutboundEmail, request.outbound_email_id, with_for_update=True)
            if request.outbound_email_id
            else None
        )
        if outbound is None:
            outbound = OutboundService.create_draft(
                session,
                purpose=OutboundPurpose.SUPPLIER_ORDER,
                to_addresses=[to],
                subject=subject,
                body_text=text,
                counterparty_id=purchase.counterparty_id,
            )
        else:  # след „Отмени“: същото писмо (същият АИТ номер), с актуалните редове
            outbound.to_addresses = [to]
            outbound.subject = smtp.subject_with_reference(subject, outbound.reference_code)
            outbound.body_text = text
        OutboundService.queue(session, outbound)
        request.outbound_email_id = outbound.id
        request.status = RequestStatus.QUEUED.value
        card.outbound_email_id = outbound.id
        CardService.resolve(session, card, CardStatus.DONE, CardResolution.SENT)
        outbound_service.schedule_send(session, outbound)
        supplier = session.get(Counterparty, purchase.counterparty_id)
        return ActionResult(
            f"Имейлът е изпратен до {supplier.name if supplier else to}", make_undo_token(outbound.id)
        )

    @staticmethod
    def reject(session: Session, card: ActionCard, body: dict[str, Any]) -> ActionResult:
        if card.client_request_id is None:
            raise DomainError("not_found", "Картата няма заявка.", http_status=404)
        request = RequestService.get(session, card.client_request_id, lock=True)
        RequestService._require_draft(request)
        request.status = RequestStatus.REJECTED.value
        CardService.resolve(session, card, CardStatus.DONE, CardResolution.REJECTED)
        return ActionResult("Заявката е отхвърлена")

    # ---------- undo ----------

    @staticmethod
    def undo(session: Session, token: str) -> None:
        try:
            data = _serializer().loads(token, max_age=UNDO_MAX_AGE_SECONDS)
        except BadSignature:
            raise UndoExpired from None
        outbound = session.scalar(
            select(OutboundEmail).where(OutboundEmail.id == data["outbound_email_id"]).with_for_update()
        )
        if outbound is None or outbound.status != OutboundStatus.QUEUED.value:
            raise UndoExpired
        outbound.status = OutboundStatus.DRAFT.value
        outbound.send_after = None
        for request in session.scalars(
            select(ClientRequest).where(
                ClientRequest.outbound_email_id == outbound.id,
                ClientRequest.status == RequestStatus.QUEUED.value,
            )
        ):
            request.status = RequestStatus.DRAFT.value
        for card in session.scalars(select(ActionCard).where(ActionCard.outbound_email_id == outbound.id)):
            if card.status != CardStatus.OPEN.value:
                card.status = CardStatus.OPEN.value
                card.resolution = None
                card.resolved_at = None
                card.resolved_by_id = None
        session.flush()

    # ---------- finalize ----------

    @staticmethod
    def finalize(session: Session, outbound: OutboundEmail) -> None:
        """Кука на `mail.send_outbound` (в неговата транзакция, преди SMTP): курсове `planned` + събития."""
        request = session.scalar(
            select(ClientRequest).where(ClientRequest.outbound_email_id == outbound.id).with_for_update()
        )
        if request is None:
            return
        sale = session.get(Contract, request.sale_contract_id) if request.sale_contract_id else None
        purchase = RequestService._purchase(session, request)
        if sale is None or purchase is None:
            raise DomainError("validation_error", "Заявката няма договори.", http_status=400)
        trips = TripService(session)
        for line in RequestService.lines(session, request):
            trip = trips.create_planned(
                PlannedTripData(
                    request_id=request.id,
                    sale_contract=sale,
                    purchase_contract=purchase,
                    planned_date=line.planned_date,
                    plate=line.plate_raw,
                    planned_qty_t=line.planned_qty_t,
                    driver_name=line.driver_name,
                    driver_phone=line.driver_phone,
                    inbound_email_id=request.inbound_email_id,
                    outbound_email_id=outbound.id,
                )
            )
            line.trip_id = trip.id
        request.status = RequestStatus.SENT.value
        session.flush()
        # TODO(T4.3): check_over(sale_contract) — виж docstring на модула.


def register() -> None:
    register_action(CardKind.CLIENT_REQUEST, "send", RequestService.send)
    register_action(CardKind.CLIENT_REQUEST, "reject", RequestService.reject)
    outbound_service.register_before_send(OutboundPurpose.SUPPLIER_ORDER, RequestService.finalize)


register()
