"""RequestService — заявки от клиенти: редакция, „Изпрати“, „Отмени“, `finalize` (docs/api.md §2–§3).

- Тема, приветствие и адреси на писмото са по доставчик: `client_request_supplier_drafts` (ред само при
  редакция; NULL = подразбиращото се). `payload` на картата държи само подразбираните (`subject`, `greeting`).
- „Изпрати“ обработва само редовете С доставчик, които още не са тръгнали; заявката става `queued`, когато
  няма друг ред за изпращане (иначе остава `draft`, картата — `open`). `undo` връща само писмата от токена.
- `send` НЕ създава курсове: слага заявката и писмото в `queued`; курсовете се създават от `finalize`,
  която `mail.send_outbound` вика в същата транзакция преди SMTP (регистрира се през `register_before_send`).
- `undo` връща писмото и заявката в `draft`, а картата в `open`.
- След `finalize` се преизчислява `contract_over` на продажния договор (`contract_alerts.check_over`).
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
from app.domain.emails import normalize_email
from app.domain.order_format import render_body
from app.domain.plates import format_plate, is_valid_plate, normalize_plate
from app.mail import smtp
from app.models.cards import ActionCard, CardKind, CardResolution, CardStatus
from app.models.company import CompanySettings
from app.models.contracts import Contract
from app.models.mail import OutboundEmail, OutboundPurpose, OutboundStatus
from app.models.masterdata import Contact, Counterparty, Warehouse
from app.models.trips import ClientRequest, ClientRequestLine, ClientRequestSupplierDraft, RequestStatus
from app.schemas.cards import ClientRequestLineView, ClientRequestPayload
from app.schemas.requests import (
    EmailPreviewDTO,
    GroupEmailDTO,
    LineBody,
    OfferDTO,
    PatchEmailBody,
    PreviewDTO,
    RequestDTO,
    RequestLineDTO,
    SupplierGroupDTO,
    WarehouseRef,
)
from app.services import outbound as outbound_service
from app.services.action_gateway import ProposalSpec
from app.services.cards import ActionResult, CardService, register_action
from app.services.contract_alerts import check_over
from app.services.offers import best_offer
from app.services.outbound import OutboundService
from app.services.stock import check_stock_low
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


def make_undo_token(outbound_ids: list[int]) -> str:
    return _serializer().dumps({"outbound_email_ids": outbound_ids})


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
    def line_purchase(session: Session, request: ClientRequest, line: ClientRequestLine) -> Contract | None:
        """Покупният договор на реда; за стари заявки (без оферти) — насрещният на продажния."""
        if line.purchase_contract_id is not None:
            return session.get(Contract, line.purchase_contract_id)
        return RequestService._purchase(session, request)

    @staticmethod
    def first_purchase(
        session: Session, request: ClientRequest, lines: list[ClientRequestLine]
    ) -> Contract | None:
        """Покупният договор на първия ред с доставчик (за „До:“ в картата, докато писмата са няколко)."""
        for line in lines:
            if (purchase := RequestService.line_purchase(session, request, line)) is not None:
                return purchase
        return None

    @staticmethod
    def groups(
        session: Session, request: ClientRequest, lines: list[ClientRequestLine]
    ) -> tuple[dict[int, tuple[Contract, list[ClientRequestLine]]], list[ClientRequestLine]]:
        """Редовете по доставчик (по реда на първата поява) и редовете без доставчик („Без склад“)."""
        out: dict[int, tuple[Contract, list[ClientRequestLine]]] = {}
        unassigned: list[ClientRequestLine] = []
        for line in lines:
            contract = RequestService.line_purchase(session, request, line)
            if contract is None:
                unassigned.append(line)
            else:
                out.setdefault(contract.counterparty_id, (contract, []))[1].append(line)
        return out, unassigned

    @staticmethod
    def sent_line_ids(session: Session, lines: list[ClientRequestLine]) -> set[int]:
        """Редове, чието писмо вече е `queued`/`sent`/… (не е `draft`) — не се пипат и не се пращат пак."""
        ids = [line.outbound_email_id for line in lines if line.outbound_email_id is not None]
        if not ids:
            return set()
        rows = session.execute(
            select(OutboundEmail.id, OutboundEmail.status).where(OutboundEmail.id.in_(ids))
        )
        statuses: dict[int, str] = dict(rows.all())  # type: ignore[arg-type]
        return {
            line.id
            for line in lines
            if line.outbound_email_id is not None
            and statuses.get(line.outbound_email_id, OutboundStatus.DRAFT.value) != OutboundStatus.DRAFT.value
        }

    @staticmethod
    def supplier_draft(
        session: Session, request: ClientRequest, supplier_id: int
    ) -> ClientRequestSupplierDraft | None:
        return session.scalar(
            select(ClientRequestSupplierDraft).where(
                ClientRequestSupplierDraft.request_id == request.id,
                ClientRequestSupplierDraft.counterparty_id == supplier_id,
            )
        )

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
    def addresses(
        session: Session, request: ClientRequest, purchase: Contract, draft: ClientRequestSupplierDraft | None
    ) -> list[str]:
        """„До:“ на писмото: редактираното, иначе контактът за поръчки на договора."""
        if draft is not None and draft.to_addresses:
            return list(draft.to_addresses)
        default = RequestService._order_address(session, purchase)
        return [default] if default else []

    @staticmethod
    def to_dto(session: Session, request: ClientRequest) -> RequestDTO:
        lines = RequestService.lines(session, request)
        sale = session.get(Contract, request.sale_contract_id) if request.sale_contract_id else None
        purchases = [RequestService.line_purchase(session, request, line) for line in lines]
        refs = RequestService._warehouse_refs(session, [line.warehouse_id for line in lines])
        warnings = [
            f"Агентът не е сигурен за {_FIELD_LABELS.get(f, f)} на {format_plate(line.plate_raw)}"
            + (f" — {line.uncertain_note}" if line.uncertain_note else "")
            for line in lines
            for f in line.uncertain_fields
        ]
        subject = RequestService.default_subject(session, request, lines)
        greeting = RequestService.default_greeting(session, request, lines)
        by_supplier, unassigned = RequestService.groups(session, request, lines)
        groups: list[SupplierGroupDTO] = []
        for supplier_id, (purchase, group) in by_supplier.items():
            supplier = session.get(Counterparty, supplier_id)
            draft = RequestService.supplier_draft(session, request, supplier_id)
            groups.append(
                SupplierGroupDTO(
                    supplier_id=supplier_id,
                    supplier_name=supplier.name if supplier is not None else None,
                    line_ids=[line.id for line in group],
                    email=GroupEmailDTO(
                        to=RequestService.addresses(session, request, purchase, draft),
                        subject=(draft.subject if draft else None) or subject,
                        body_intro=(draft.body_intro if draft else None) or greeting,
                        version=draft.version if draft else 1,
                    ),
                )
            )
        if unassigned:
            groups.append(
                SupplierGroupDTO(
                    supplier_id=None, supplier_name=None, line_ids=[x.id for x in unassigned], email=None
                )
            )
        return RequestDTO(
            id=request.id,
            status=request.status,
            customer_name=RequestService._customer_name(session, request),
            sale_contract=(
                {"id": sale.id, "number": sale.number} if sale is not None else None  # type: ignore[arg-type]
            ),
            lines=[
                RequestLineDTO(
                    id=line.id,
                    line_no=line.line_no,
                    planned_date=line.planned_date,
                    plate="транспорт Ива" if line.own_transport else format_plate(line.plate_raw),
                    driver_name=line.driver_name,
                    planned_qty_t=line.planned_qty_t,
                    uncertain_fields=line.uncertain_fields,
                    uncertain_note=line.uncertain_note,
                    packaging=line.packaging,
                    warehouse=refs.get(line.warehouse_id) if line.warehouse_id else None,
                    purchase_contract_id=line.purchase_contract_id,
                    own_transport=line.own_transport,
                    truck_type=line.truck_type,
                    needs_choice=purchase is None,
                    offer_note=line.offer_note,
                )
                for line, purchase in zip(lines, purchases, strict=True)
            ],
            groups=groups,
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
    def _refresh_card(session: Session, request: ClientRequest) -> None:
        card = RequestService.card(session, request)
        if card is None or card.status != CardStatus.OPEN.value:
            return
        lines = RequestService.lines(session, request)
        purchase = RequestService.first_purchase(session, request, lines)
        payload = ClientRequestPayload(
            request_id=request.id,
            inbound_email_id=request.inbound_email_id,
            to=RequestService._order_address(session, purchase) or card.payload.get("to", ""),
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
            total_qty_t=sum((line.planned_qty_t for line in lines), Decimal("0")),
            has_uncertain=any(line.uncertain_fields for line in lines),
        )
        card.payload = payload.model_dump(mode="json")
        session.flush()

    @staticmethod
    def update_email(
        session: Session, request: ClientRequest, supplier_id: int, body: PatchEmailBody
    ) -> ClientRequestSupplierDraft:
        """Отмяна на писмото до един доставчик; `version` е на писмото, не на заявката."""
        RequestService._require_draft(request)
        lines = RequestService.lines(session, request)
        by_supplier, _ = RequestService.groups(session, request, lines)
        if supplier_id not in by_supplier:
            raise DomainError("not_found", "Доставчикът няма писмо в тази заявка.", http_status=404)
        group = by_supplier[supplier_id][1]
        if RequestService.sent_line_ids(session, group) >= {line.id for line in group}:
            raise DomainError("invalid_transition", "Писмото вече е изпратено.", http_status=409)
        draft = RequestService.supplier_draft(session, request, supplier_id)
        if (draft.version if draft else 1) != body.version:
            raise DomainError("version_conflict", "Писмото е променено от друг.", http_status=409)
        if draft is None:
            draft = ClientRequestSupplierDraft(request_id=request.id, counterparty_id=supplier_id)
            session.add(draft)
            session.flush()  # версия 1; промените по-долу са UPDATE → 2
        sent = body.model_fields_set
        if "to" in sent:
            addresses = [normalize_email(a) for a in body.to or []]
            if any("@" not in a for a in addresses):
                msg = "Невалиден имейл адрес."
                raise DomainError("validation_error", msg, http_status=400, fields={"to": msg})
            draft.to_addresses = addresses or None
        if "subject" in sent:
            draft.subject = body.subject
        if "body_intro" in sent:
            draft.body_intro = body.body_intro or None
        draft.updated_at = clock.now()  # и PATCH без промяна вдига версията на писмото
        session.flush()
        return draft

    @staticmethod
    def _apply(session: Session, request: ClientRequest, line: ClientRequestLine, body: LineBody) -> None:
        if not body.own_transport and not is_valid_plate(body.plate):
            raise InvalidPlate
        changed = {
            "planned_date": line.planned_date != body.planned_date,
            "plate": line.plate_norm != normalize_plate(body.plate),
            "driver_name": line.driver_name != body.driver_name,
            "planned_qty_t": line.planned_qty_t != body.planned_qty_t,
        }
        if "truck_type" in body.model_fields_set:
            line.truck_type = body.truck_type or None
        line.planned_date = body.planned_date
        line.plate_raw = body.plate
        line.plate_norm = normalize_plate(body.plate)
        line.driver_name = body.driver_name
        line.planned_qty_t = body.planned_qty_t
        line.own_transport = body.own_transport
        sent = body.model_fields_set
        packaging_changed = "packaging" in sent and body.packaging != line.packaging
        if "packaging" in sent:
            line.packaging = body.packaging
        if "warehouse_id" in sent and (body.warehouse_id != line.warehouse_id or packaging_changed):
            RequestService._choose_warehouse(session, request, line, body.warehouse_id)
        elif packaging_changed:
            RequestService._auto_offer(session, request, line)
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
    def _require_unsent(session: Session, line: ClientRequestLine) -> None:
        if line.id in RequestService.sent_line_ids(session, [line]):
            raise DomainError(
                "invalid_transition", "Редът е в изпратено писмо — първо „Отмени“.", http_status=409
            )

    @staticmethod
    def _sale(session: Session, request: ClientRequest) -> Contract | None:
        return session.get(Contract, request.sale_contract_id) if request.sale_contract_id else None

    @staticmethod
    def _choose_warehouse(
        session: Session, request: ClientRequest, line: ClientRequestLine, warehouse_id: int | None
    ) -> None:
        """Ива избира склад ръчно: договорът е най-евтината оферта в него; без оферта (или `None`) — ред
        „избери“. Не е грешка."""
        sale = RequestService._sale(session, request)
        line.warehouse_id = warehouse_id
        line.purchase_contract_id = None
        line.offer_note = {"manual": True, "chosen": None}
        if warehouse_id is None or sale is None or line.packaging is None:
            return
        result = best_offer(
            session,
            product_id=sale.product_id,
            packaging=line.packaging,
            on=line.planned_date,
            warehouse_id=warehouse_id,
        )
        line.purchase_contract_id = result.best.contract_id if result.best else None
        line.offer_note = {"manual": True, **result.note()}

    @staticmethod
    def _auto_offer(session: Session, request: ClientRequest, line: ClientRequestLine) -> None:
        """Ново предложение по цена (след смяна на разфасовка)."""
        sale = RequestService._sale(session, request)
        line.warehouse_id = None
        line.purchase_contract_id = None
        line.offer_note = None
        if sale is None or line.packaging is None:
            return
        result = best_offer(
            session, product_id=sale.product_id, packaging=line.packaging, on=line.planned_date
        )
        line.offer_note = result.note()
        if result.best is not None:
            line.warehouse_id = result.best.warehouse_id
            line.purchase_contract_id = result.best.contract_id

    @staticmethod
    def line_offers(session: Session, request: ClientRequest, line_id: int) -> list[OfferDTO]:
        """Допустимите оферти за реда (екран 21), най-евтината първа."""
        line = RequestService._line(session, request, line_id)
        sale = RequestService._sale(session, request)
        if sale is None or line.packaging is None:
            return []
        offers = best_offer(
            session, product_id=sale.product_id, packaging=line.packaging, on=line.planned_date
        ).candidates
        refs = RequestService._warehouse_refs(session, [o.warehouse_id for o in offers])
        return [
            OfferDTO(
                warehouse=refs[o.warehouse_id],
                contract_id=o.contract_id,
                price_per_t=o.price_per_t,
                is_cheapest=i == 0,
            )
            for i, o in enumerate(offers)
        ]

    @staticmethod
    def _warehouse_refs(session: Session, ids: list[int | None]) -> dict[int, WarehouseRef]:
        wanted = {i for i in ids if i is not None}
        if not wanted:
            return {}
        rows = session.execute(
            select(Warehouse.id, Warehouse.name, Counterparty.name)
            .join(Counterparty, Counterparty.id == Warehouse.counterparty_id)
            .where(Warehouse.id.in_(wanted))
        )
        return {wid: WarehouseRef(id=wid, name=wname, supplier_name=cname) for wid, wname, cname in rows}

    @staticmethod
    def update_line(
        session: Session, request: ClientRequest, line_id: int, body: LineBody
    ) -> ClientRequestLine:
        RequestService._require_draft(request)
        line = RequestService._line(session, request, line_id)
        RequestService._require_unsent(session, line)
        RequestService._apply(session, request, line, body)
        RequestService._touch(session, request)
        return line

    @staticmethod
    def add_line(session: Session, request: ClientRequest, body: LineBody) -> ClientRequestLine:
        RequestService._require_draft(request)
        if not body.own_transport and not is_valid_plate(body.plate):
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
            packaging=body.packaging,
            own_transport=body.own_transport,
        )
        session.add(line)
        session.flush()
        if body.warehouse_id is not None:
            RequestService._choose_warehouse(session, request, line, body.warehouse_id)
        elif body.packaging is not None:
            RequestService._auto_offer(session, request, line)
        RequestService._touch(session, request)
        return line

    @staticmethod
    def remove_line(session: Session, request: ClientRequest, line_id: int) -> None:
        RequestService._require_draft(request)
        line = RequestService._line(session, request, line_id)
        RequestService._require_unsent(session, line)
        line.is_removed = True
        RequestService._touch(session, request)

    # ---------- действия по картата ----------

    @staticmethod
    def compose(
        session: Session,
        request: ClientRequest,
        lines: list[ClientRequestLine],
        supplier: Counterparty,
    ) -> tuple[str, str]:
        """`(subject, body_text)` за един доставчик — единственото място, което сглобява писмото."""
        draft = RequestService.supplier_draft(session, request, supplier.id)
        subject = (draft.subject if draft else None) or RequestService.default_subject(
            session, request, lines
        )
        greeting = (draft.body_intro if draft else None) or RequestService.default_greeting(
            session, request, lines
        )
        sale = RequestService._sale(session, request)
        client = session.get(Counterparty, sale.counterparty_id) if sale is not None else None
        truck = client.truck_type if client is not None else None
        return subject, render_body(supplier.order_format, greeting, lines, truck)

    @staticmethod
    def _drafts(
        session: Session, request: ClientRequest
    ) -> tuple[list[tuple[Counterparty, list[str], list[ClientRequestLine], str, str]], bool]:
        """По едно писмо на доставчик за редовете с доставчик, които още не са тръгнали:
        `([(доставчик, адреси, редове, тема, тяло)], има_ли_още_редове_за_после)`."""
        all_lines = RequestService.lines(session, request)
        if request.sale_contract_id is None:
            raise DomainError("validation_error", "Изберете договор за заявката.", http_status=400)
        if not all_lines:
            raise DomainError("validation_error", "Заявката няма камиони.", http_status=400)
        sent = RequestService.sent_line_ids(session, all_lines)
        by_supplier, unassigned = RequestService.groups(
            session, request, [line for line in all_lines if line.id not in sent]
        )
        if not by_supplier:
            raise DomainError(
                "validation_error", "Няма нито един ред с избран склад/доставчик.", http_status=400
            )
        drafts = []
        for supplier_id, (purchase, group) in by_supplier.items():
            supplier = session.get(Counterparty, supplier_id)
            draft = RequestService.supplier_draft(session, request, supplier_id)
            to = RequestService.addresses(session, request, purchase, draft)
            if supplier is None or not to:
                name = supplier.name if supplier is not None else str(supplier_id)
                raise DomainError(
                    "validation_error", f"Доставчикът {name} няма адрес за поръчки.", http_status=400
                )
            subject, text = RequestService.compose(session, request, group, supplier)
            drafts.append((supplier, to, group, subject, text))
        return drafts, bool(unassigned)

    @staticmethod
    def preview(session: Session, request: ClientRequest) -> PreviewDTO:
        """Реалните имейли (по един на доставчик) без `OutboundEmail` и без АИТ номер (placeholder)."""
        RequestService._require_draft(request)
        company = session.get(CompanySettings, 1)
        signature = company.email_signature if company is not None else ""
        drafts, _ = RequestService._drafts(session, request)
        return PreviewDTO(
            emails=[
                EmailPreviewDTO(
                    supplier_name=supplier.name,
                    to=", ".join(to),
                    subject=smtp.subject_with_reference(subject, "АИТ-…"),
                    body_text=smtp.render_body(text, signature),
                )
                for supplier, to, _group, subject, text in drafts
            ]
        )

    @staticmethod
    def propose_send(session: Session, card: ActionCard, body: dict[str, Any]) -> ProposalSpec:
        """Съветен режим: същите писма като `preview` (същият `compose`); нищо не се изпраща."""
        if card.client_request_id is None:
            raise DomainError("not_found", "Картата няма заявка.", http_status=404)
        request = RequestService.get(session, card.client_request_id)
        emails = RequestService.preview(session, request).emails
        count = len(emails)
        title = "Бих изпратила 1 писмо" if count == 1 else f"Бих изпратила {count} писма"
        return ProposalSpec(
            kind="send_request",
            title=title,
            payload={"emails": [e.model_dump(mode="json") for e in emails]},
            deal_ref=f"client_requests:{request.id}",
        )

    @staticmethod
    def send(session: Session, card: ActionCard, body: dict[str, Any]) -> ActionResult:
        if card.client_request_id is None:
            raise DomainError("not_found", "Картата няма заявка.", http_status=404)
        request = RequestService.get(session, card.client_request_id, lock=True)
        RequestService._require_draft(request)
        drafts, has_rest = RequestService._drafts(session, request)

        outbounds: list[OutboundEmail] = []
        for supplier, to, group, subject, text in drafts:
            existing = next((line.outbound_email_id for line in group if line.outbound_email_id), None)
            outbound = session.get(OutboundEmail, existing, with_for_update=True) if existing else None
            if outbound is None:
                outbound = OutboundService.create_draft(
                    session,
                    purpose=OutboundPurpose.SUPPLIER_ORDER,
                    to_addresses=to,
                    subject=subject,
                    body_text=text,
                    counterparty_id=supplier.id,
                )
            else:  # след „Отмени“: същото писмо (същият АИТ номер), с актуалните редове
                outbound.to_addresses = to
                outbound.subject = smtp.subject_with_reference(subject, outbound.reference_code)
                outbound.body_text = text
            for line in group:
                line.outbound_email_id = outbound.id
            OutboundService.queue(session, outbound)
            outbounds.append(outbound)
        first = outbounds[0]
        request.outbound_email_id = first.id
        if not has_rest:  # няма ред без склад → всичко е тръгнало; иначе заявката и картата остават отворени
            request.status = RequestStatus.QUEUED.value
            card.outbound_email_id = first.id
            CardService.resolve(session, card, CardStatus.DONE, CardResolution.SENT)
        for outbound in outbounds:
            outbound_service.schedule_send(session, outbound)
        names = ", ".join(supplier.name for supplier, *_ in drafts)
        return ActionResult(f"Имейлът е изпратен до {names}", make_undo_token([o.id for o in outbounds]))

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
        """Връща в `draft` само писмата от токена (с редовете им); писма от други „Изпрати“ не се пипат."""
        try:
            data = _serializer().loads(token, max_age=UNDO_MAX_AGE_SECONDS)
        except BadSignature:
            raise UndoExpired from None
        ids = set(data["outbound_email_ids"])
        mails = list(
            session.scalars(select(OutboundEmail).where(OutboundEmail.id.in_(ids)).with_for_update())
        )
        if len(mails) != len(ids) or any(m.status != OutboundStatus.QUEUED.value for m in mails):
            raise UndoExpired  # поне едно вече е тръгнало — отмяна на половината е по-лоша
        for mail in mails:
            mail.status = OutboundStatus.DRAFT.value
            mail.send_after = None
        request_ids = set(
            session.scalars(
                select(ClientRequestLine.request_id).where(ClientRequestLine.outbound_email_id.in_(ids))
            )
        )
        for request in session.scalars(
            select(ClientRequest).where(
                ClientRequest.id.in_(request_ids), ClientRequest.status == RequestStatus.QUEUED.value
            )
        ):
            request.status = RequestStatus.DRAFT.value
        for card in session.scalars(select(ActionCard).where(ActionCard.outbound_email_id.in_(ids))):
            if card.status != CardStatus.OPEN.value:
                card.status = CardStatus.OPEN.value
                card.resolution = None
                card.resolved_at = None
                card.resolved_by_id = None
        session.flush()

    # ---------- finalize ----------

    @staticmethod
    def finalize(session: Session, outbound: OutboundEmail) -> None:
        """Кука на `mail.send_outbound` (в неговата транзакция, преди SMTP): курсове `planned` за редовете
        на това писмо; заявката става `sent`, когато всички нейни писма са тръгнали."""
        mine = list(
            session.scalars(
                select(ClientRequestLine)
                .where(
                    ClientRequestLine.outbound_email_id == outbound.id,
                    ClientRequestLine.is_removed.is_(False),
                )
                .order_by(ClientRequestLine.line_no)
            )
        )
        request = (
            session.scalar(
                select(ClientRequest).where(ClientRequest.id == mine[0].request_id).with_for_update()
            )
            if mine
            else session.scalar(
                select(ClientRequest).where(ClientRequest.outbound_email_id == outbound.id).with_for_update()
            )
        )
        if request is None:
            return
        sale = session.get(Contract, request.sale_contract_id) if request.sale_contract_id else None
        if sale is None:
            raise DomainError("validation_error", "Заявката няма договори.", http_status=400)
        trips = TripService(session)
        for line in mine or RequestService.lines(session, request):
            purchase = RequestService.line_purchase(session, request, line)
            if purchase is None:
                raise DomainError("validation_error", "Заявката няма договори.", http_status=400)
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
                    warehouse_id=line.warehouse_id,
                    own_transport=line.own_transport,
                )
            )
            line.trip_id = trip.id
        session.flush()
        pending = session.scalar(
            select(ClientRequestLine.id)
            .where(
                ClientRequestLine.request_id == request.id,
                ClientRequestLine.is_removed.is_(False),
                ClientRequestLine.trip_id.is_(None),
            )
            .limit(1)
        )
        if pending is None:
            request.status = RequestStatus.SENT.value
        session.flush()
        check_stock_low(session, sale.product_id)
        check_over(session, sale)


def register() -> None:
    register_action(
        CardKind.CLIENT_REQUEST, "send", RequestService.send, advisory=RequestService.propose_send
    )
    register_action(CardKind.CLIENT_REQUEST, "reject", RequestService.reject)
    outbound_service.register_before_send(OutboundPurpose.SUPPLIER_ORDER, RequestService.finalize)


register()
