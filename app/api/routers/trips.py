from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app.api.deps import CurrentPrincipal, DbSession
from app.models.trips import CancelSource
from app.schemas.trips import CancelTripRequest, LinkContractRequest, TripDetailDTO, TripListDTO
from app.services.trip_views import check_version, get_trip, list_trips, trip_detail
from app.services.trips import TripService

router = APIRouter(tags=["trips"])


@router.get("/trips", response_model=TripListDTO)
def trips_list(
    _: CurrentPrincipal,
    session: DbSession,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    status: Literal["all", "planned", "completed", "cancelled", "unplanned"] = "all",
    contract_id: int | None = None,
) -> TripListDTO:
    return list_trips(session, date_from=date_from, date_to=date_to, status=status, contract_id=contract_id)


@router.get("/trips/{trip_id}", response_model=TripDetailDTO)
def trip_get(trip_id: int, _: CurrentPrincipal, session: DbSession) -> TripDetailDTO:
    return trip_detail(session, get_trip(session, trip_id))


@router.post("/trips/{trip_id}/cancel", response_model=TripDetailDTO)
def trip_cancel(
    trip_id: int, body: CancelTripRequest, _: CurrentPrincipal, session: DbSession
) -> TripDetailDTO:
    trip = get_trip(session, trip_id)
    check_version(trip, body.version)
    TripService(session).cancel(trip, source=CancelSource.USER, reason=body.reason)
    # TODO(T4.3): преизчисляване на картата contract_over — помощникът още не съществува.
    return trip_detail(session, trip)


@router.post("/trips/{trip_id}/link-contract", response_model=TripDetailDTO)
def trip_link_contract(
    trip_id: int, body: LinkContractRequest, _: CurrentPrincipal, session: DbSession
) -> TripDetailDTO:
    trip = get_trip(session, trip_id)
    check_version(trip, body.version)
    TripService(session).link_to_contract(trip, body.sale_contract_id)
    return trip_detail(session, trip)
