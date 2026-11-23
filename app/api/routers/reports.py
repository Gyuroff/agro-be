from fastapi import APIRouter

from app.api.deps import CurrentPrincipal, DbSession
from app.schemas.reports import ReportDTO
from app.services import reports as _reports  # noqa: F401  (регистрира действията по карта)
from app.services.report_views import get_report, report_dto

router = APIRouter(tags=["reports"])


@router.get("/reports/{report_id}", response_model=ReportDTO)
def report_get(report_id: int, _: CurrentPrincipal, session: DbSession) -> ReportDTO:
    return report_dto(session, get_report(session, report_id))
