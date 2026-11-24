from collections.abc import Iterable

from fastapi import FastAPI

from app.admin.base import AuditedModelView
from app.admin.setup import setup_admin
from app.admin.views import ALL_VIEWS
from app.api.routers import (
    auth,
    cards,
    contracts,
    counterparties,
    emails,
    health,
    me,
    reports,
    requests,
    result,
    trips,
)
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import RequestIdMiddleware, configure_logging


def create_app(admin_views: Iterable[type[AuditedModelView]] = ALL_VIEWS) -> FastAPI:
    """Фабрика (uvicorn app.main:create_app --factory).

    Тук се монтират: routers под /api/v1, /admin (SQLAdmin), middleware — със следващите задачи.
    """
    settings = get_settings()  # гърми рано, ако липсва задължителна настройка
    configure_logging("DEBUG" if settings.env == "dev" else "INFO")

    app = FastAPI(title="Agro ERP", version="0.1.0")
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(me.router, prefix="/api/v1")
    app.include_router(cards.router, prefix="/api/v1")
    app.include_router(trips.router, prefix="/api/v1")
    app.include_router(contracts.router, prefix="/api/v1")
    app.include_router(counterparties.router, prefix="/api/v1")
    app.include_router(requests.router, prefix="/api/v1")
    app.include_router(emails.router, prefix="/api/v1")
    app.include_router(reports.router, prefix="/api/v1")
    app.include_router(result.router, prefix="/api/v1")
    setup_admin(app, admin_views)
    return app
