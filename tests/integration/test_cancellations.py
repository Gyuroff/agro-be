"""Карта „Отказани курсове“: confirm {notify_supplier} и keep (agro-br9.8)."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.audit_context import AuditContext
from app.core.errors import DomainError
from app.models.cards import ActionCard, CardKind, CardResolution, CardStatus
from app.models.contracts import Contract
from app.models.mail import InboundEmail, OutboundEmail, OutboundPurpose, OutboundStatus
from app.models.masterdata import Contact, Counterparty, Product, Warehouse
from app.models.trips import ClientRequest, Trip, TripStatus
from app.schemas.cards import CancellationPayload
from app.services.cards import CardService
from app.services.trips import CancelSource, PlannedTripData, TripService

D = Decimal
DAY = date(2026, 11, 13)


class World:
    def __init__(self, session: Session, *, contact: bool = True) -> None:
        self.session = session
        client = Counterparty(name="Агро Трейд", legal_name="Агро Трейд ООД", eik="111111111")
        supplier = Counterparty(name="Пелет АД", legal_name="Пелет АД", eik="222222222")
        product = Product(name="Пелети")
        session.add_all([client, supplier, product])
        session.flush()
        wh = Warehouse(counterparty_id=supplier.id, name="Тервел")
        session.add(wh)
        session.flush()
        order = None
        if contact:
            order = Contact(counterparty_id=supplier.id, email="orders@pelet.bg", source="manual")
            session.add(order)
            session.flush()
        self.sale = self._contract("sale", "2026-018", client, product)
        self.purchase = self._contract(
            "purchase",
            "П-2026-007",
            supplier,
            product,
            warehouse_id=wh.id,
            order_contact_id=order.id if order else None,
        )

        mail = InboundEmail(
            message_id="<c@x>",
            from_address="a@b.bg",
            received_at=clock.now(),
            raw_path="/tmp/x.eml",
            auth_ok=True,
        )
        session.add(mail)
        session.flush()
        self.request = ClientRequest(inbound_email_id=mail.id, sale_contract_id=self.sale.id)
        session.add(self.request)
        session.flush()

    def _contract(self, side: str, number: str, cp: Counterparty, product: Product, **kw: object) -> Contract:
        c = Contract(
            number=number, side=side, counterparty_id=cp.id, product_id=product.id,
            valid_from=date(2026, 1, 1), **kw,
        )  # fmt: skip
        self.session.add(c)
        self.session.flush()
        return c

    def trip(self, plate: str) -> Trip:
        return TripService(self.session).create_planned(
            PlannedTripData(
                request_id=self.request.id, sale_contract=self.sale, purchase_contract=self.purchase,
                planned_date=DAY, plate=plate, planned_qty_t=D("25.000"),
            )
        )  # fmt: skip

    def card(self, trips: list[Trip], *, notify: bool = True) -> ActionCard:
        return CardService.create(
            self.session,
            CardKind.CANCELLATION,
            title="Отказани курсове",
            payload=CancellationPayload(
                trip_ids=[t.id for t in trips], date=DAY, plates=[t.plate_display for t in trips],
                supplier_name="Пелет АД", notify_supplier=notify,
            ),
        )  # fmt: skip


@pytest.fixture
def w(db_session: Session, audit_ctx: AuditContext, live_mode: None) -> World:
    return World(db_session)


def _outbound(db: Session) -> list[OutboundEmail]:
    return list(db.scalars(select(OutboundEmail)))


def test_confirm_with_notify_cancels_and_queues_letter(w: World, db_session: Session) -> None:
    trips = [w.trip("СВ 7890 ТР"), w.trip("РВ 1122 АВ")]
    card = w.card(trips)
    _, result = CardService.perform_action(db_session, card.id, "confirm", {"notify_supplier": True})
    assert all(t.status == TripStatus.CANCELLED.value and t.cancel_source == "email" for t in trips)
    assert card.status == CardStatus.DONE.value and card.resolution == CardResolution.CONFIRMED.value
    (mail,) = _outbound(db_session)
    assert mail.purpose == OutboundPurpose.SUPPLIER_CANCELLATION.value
    assert mail.status == OutboundStatus.QUEUED.value and mail.to_addresses == ["orders@pelet.bg"]
    assert mail.subject.startswith("Отказани курсове 13.11.2026 — Агро Трейд [АИТ-")
    assert "СВ 7890 ТР" in mail.body_text
    assert mail.send_after is not None and mail.send_after <= clock.now()
    assert result.undo_token is None and result.snackbar is not None


def test_confirm_without_notify_sends_nothing(w: World, db_session: Session) -> None:
    trips = [w.trip("СВ 7890 ТР")]
    card = w.card(trips)
    CardService.perform_action(db_session, card.id, "confirm", {"notify_supplier": False})
    assert trips[0].status == TripStatus.CANCELLED.value
    assert _outbound(db_session) == []


def test_mixed_statuses_only_planned_cancelled(w: World, db_session: Session) -> None:
    planned, done = w.trip("СВ 7890 ТР"), w.trip("РВ 1122 АВ")
    TripService(db_session).cancel(done, source=CancelSource.USER)  # вече не е planned
    card = w.card([planned, done])
    _, result = CardService.perform_action(db_session, card.id, "confirm", {"notify_supplier": False})
    assert planned.status == TripStatus.CANCELLED.value and done.cancel_source == "user"
    assert result.snackbar is not None and "1 курс вече е изпълнен и не е отказан" in result.snackbar


def test_keep_dismisses_and_leaves_trips(w: World, db_session: Session) -> None:
    trips = [w.trip("СВ 7890 ТР")]
    card = w.card(trips)
    CardService.perform_action(db_session, card.id, "keep", {})
    assert card.status == CardStatus.DISMISSED.value and card.resolution == CardResolution.KEPT.value
    assert trips[0].status == TripStatus.PLANNED.value and _outbound(db_session) == []


def test_notify_without_order_contact_is_validation_error(
    db_session: Session, audit_ctx: AuditContext, live_mode: None
) -> None:
    w = World(db_session, contact=False)
    trips = [w.trip("СВ 7890 ТР")]
    card = w.card(trips)
    with pytest.raises(DomainError) as err:
        CardService.perform_action(db_session, card.id, "confirm", {"notify_supplier": True})
    assert err.value.code == "validation_error"
    assert trips[0].status == TripStatus.PLANNED.value


def test_advisory_cancels_but_does_not_queue_letter(w: World, db_session: Session) -> None:
    from app.models.company import CompanySettings
    from app.models.proposals import ActionProposal

    db_session.get_one(CompanySettings, 1).action_mode = "advisory"
    db_session.flush()
    trips = [w.trip("СВ 7890 ТР")]
    card = w.card(trips)
    CardService.perform_action(db_session, card.id, "confirm", {"notify_supplier": True})
    assert trips[0].status == TripStatus.CANCELLED.value
    assert _outbound(db_session) == []
    assert db_session.scalars(select(ActionProposal)).one().kind == "cancel_supplier"
