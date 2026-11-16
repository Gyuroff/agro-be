from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

from app.api.deps import CurrentPrincipal, DbSession
from app.core.errors import error_body
from app.schemas.auth import (
    DeviceDTO,
    LoginRequest,
    LoginResponse,
    RefreshRequest,
    RefreshResponse,
    UserDTO,
)
from app.services.auth import AuthFailure, AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _failure(failure: AuthFailure) -> JSONResponse:
    # Връща се (не се хвърля), за да направи get_db commit на брояча/отнетите токени.
    return JSONResponse(error_body(failure.code, failure.message_bg), status_code=failure.http_status)


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, session: DbSession) -> LoginResponse | JSONResponse:
    result = AuthService.login(session, **body.model_dump())
    if isinstance(result, AuthFailure):
        return _failure(result)
    return LoginResponse(
        access_token=result.tokens.access_token,
        access_expires_in=result.tokens.access_expires_in,
        refresh_token=result.tokens.refresh_token,
        user=UserDTO.model_validate(result.user, from_attributes=True),
        device=DeviceDTO(
            id=result.device.id, biometric_enrolled=result.device.biometric_enrolled_at is not None
        ),
    )


@router.post("/refresh", response_model=RefreshResponse)
def refresh(body: RefreshRequest, session: DbSession) -> RefreshResponse | JSONResponse:
    result = AuthService.refresh(session, refresh_token=body.refresh_token)
    if isinstance(result, AuthFailure):
        return _failure(result)
    return RefreshResponse(
        access_token=result.access_token,
        access_expires_in=result.access_expires_in,
        refresh_token=result.refresh_token,
    )


@router.post("/logout", status_code=204)
def logout(principal: CurrentPrincipal, session: DbSession) -> Response:
    AuthService.logout(session, device=principal.device)
    return Response(status_code=204)
