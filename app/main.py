from fastapi import FastAPI

from app.api.routers import auth, cards, emails, health, me, requests, trips
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import RequestIdMiddleware, configure_logging


def create_app() -> FastAPI:
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
    app.include_router(requests.router, prefix="/api/v1")
    app.include_router(emails.router, prefix="/api/v1")
    return app
