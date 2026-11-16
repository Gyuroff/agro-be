"""GET /dashboard: отворени карти, курсове за днес, последна успешна проверка на пощата."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import clock
from app.models.cards import ActionCard, CardStatus
from app.models.mail import MailboxPoll, PollStatus
from app.models.trips import Trip, TripStatus
from app.schemas.dashboard import CardDTO, DashboardDTO, TripsTodayDTO
from app.services.trip_views import trip_list_items

TODAY_ITEMS = 6


def card_dto(card: ActionCard) -> CardDTO:
    return CardDTO(
        id=card.id,
        kind=card.kind,
        title=card.title,
        subtitle=card.subtitle,
        created_at=card.created_at,
        call_phone=card.call_phone,
        payload=card.payload,
    )


def build_dashboard(session: Session) -> DashboardDTO:
    today = clock.today_sofia()
    cards = session.scalars(
        select(ActionCard)
        .where(ActionCard.status == CardStatus.OPEN.value)
        .order_by(ActionCard.created_at.desc(), ActionCard.id.desc())
    ).all()
    # „Днес“ без отказаните: не са в плана и не бива да броят в тонажа.
    today_filter = (Trip.planned_date == today, Trip.status != TripStatus.CANCELLED.value)
    count, planned = session.execute(
        select(func.count(Trip.id), func.coalesce(func.sum(Trip.planned_qty_t), 0)).where(*today_filter)
    ).one()
    trips = session.scalars(select(Trip).where(*today_filter).order_by(Trip.id).limit(TODAY_ITEMS)).all()
    last_ok = session.scalar(
        select(func.max(MailboxPoll.finished_at)).where(MailboxPoll.status == PollStatus.OK.value)
    )
    return DashboardDTO(
        today=today,
        last_mail_check_at=last_ok,
        cards=[card_dto(c) for c in cards],
        trips_today=TripsTodayDTO(
            count=count, planned_qty_t=planned, items=trip_list_items(session, trips, "long")
        ),
    )
