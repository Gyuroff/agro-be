from fastapi import APIRouter, Response

from app.api.deps import CurrentPrincipal, DbSession
from app.models.identity import PushProvider
from app.schemas.me import ConfigDTO, PushTokenRequest
from app.services.devices import DeviceService, get_app_config

router = APIRouter(prefix="/me", tags=["me"])


@router.get("/config", response_model=ConfigDTO)
def config(_principal: CurrentPrincipal, session: DbSession) -> ConfigDTO:
    settings = get_app_config(session)
    return ConfigDTO(
        app_lock_background_minutes=settings.app_lock_background_minutes,
        biometric_max_attempts=settings.biometric_max_attempts,
        company_short_name=settings.short_name,
    )


@router.post("/device/biometric-enrolled", status_code=204)
def biometric_enrolled(principal: CurrentPrincipal, session: DbSession) -> Response:
    DeviceService.mark_biometric_enrolled(session, device=principal.device)
    return Response(status_code=204)


@router.put("/device/push-token", status_code=204)
def push_token(body: PushTokenRequest, principal: CurrentPrincipal, session: DbSession) -> Response:
    DeviceService.set_push_token(
        session, device=principal.device, provider=PushProvider(body.provider), token=body.token
    )
    return Response(status_code=204)
