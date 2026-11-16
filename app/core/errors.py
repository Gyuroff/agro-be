"""Доменни грешки и формат на отговора (docs/api.md §0).

{"error": {"code": "card_not_open", "message": "Картата вече е обработена."}}
"""

from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm.exc import StaleDataError


class DomainError(Exception):
    """Бизнес грешка. Кодовете са в docs/api.md §0; съобщението е на български."""

    def __init__(self, code: str, message_bg: str, http_status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.message_bg = message_bg
        self.http_status = http_status


def error_body(code: str, message: str, fields: dict[str, str] | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if fields:
        error["fields"] = fields
    return {"error": error}


def _field_name(loc: Sequence[Any]) -> str:
    # loc = ("body", "plate") | ("query", "from") | ("path", "id") → последното име
    parts = [str(p) for p in loc if p not in ("body", "query", "path", "header")]
    return ".".join(parts) or "body"


async def _domain_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    return JSONResponse(error_body(exc.code, exc.message_bg), status_code=exc.http_status)


async def _validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    fields = {_field_name(e["loc"]): str(e["msg"]) for e in exc.errors()}
    return JSONResponse(
        error_body("validation_error", "Невалидни данни.", fields),
        status_code=400,
    )


async def _stale_data_handler(_: Request, __: Exception) -> JSONResponse:
    return JSONResponse(
        error_body("version_conflict", "Записът е променен от друг. Обновете и опитайте пак."),
        status_code=409,
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StaleDataError, _stale_data_handler)
    app.add_exception_handler(DomainError, _domain_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
