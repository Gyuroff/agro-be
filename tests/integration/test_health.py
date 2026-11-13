import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.errors import DomainError
from app.main import create_app


def test_health_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": True}


def test_health_db_down(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.routers import health

    monkeypatch.setattr(health, "_db_ok", lambda: False)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": False}


def test_request_id_generated_and_echoed(client: TestClient) -> None:
    generated = client.get("/health").headers["x-request-id"]
    assert len(generated) == 36

    echoed = client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert echoed.headers["x-request-id"] == "abc-123"


def _app_with_probe_routes() -> FastAPI:
    app = create_app()

    class Body(BaseModel):
        plate: str

    @app.get("/_probe/domain-error")
    def domain_error() -> None:  # pyright: ignore[reportUnusedFunction]
        raise DomainError("card_not_open", "Картата вече е обработена.", 409)

    @app.post("/_probe/body")
    def body(payload: Body) -> Body:  # pyright: ignore[reportUnusedFunction]
        return payload

    return app


def test_domain_error_format() -> None:
    with TestClient(_app_with_probe_routes()) as client:
        response = client.get("/_probe/domain-error")
    assert response.status_code == 409
    assert response.json() == {"error": {"code": "card_not_open", "message": "Картата вече е обработена."}}


def test_validation_error_format() -> None:
    with TestClient(_app_with_probe_routes()) as client:
        response = client.post("/_probe/body", json={})
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert "plate" in error["fields"]
