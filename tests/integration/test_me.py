"""/me/config, /me/device/biometric-enrolled, /me/device/push-token (agro-zmv.2)."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.identity import Device
from tests.integration.test_auth import api, bearer, login, user  # noqa: F401  (фикстури)


def _device(db: Session, device_id: int) -> Device:
    db.expire_all()
    device = db.get(Device, device_id)
    assert device is not None
    return device


def test_config(api: TestClient) -> None:  # noqa: F811
    tokens = login(api).json()
    response = api.get("/api/v1/me/config", headers=bearer(tokens["access_token"]))
    assert response.status_code == 200
    body = response.json()
    assert body["app_lock_background_minutes"] == 5
    assert body["biometric_max_attempts"] == 3
    assert isinstance(body["company_short_name"], str)


def test_biometric_enrolled(api: TestClient, db_session: Session) -> None:  # noqa: F811
    tokens = login(api).json()
    assert tokens["device"]["biometric_enrolled"] is False
    response = api.post("/api/v1/me/device/biometric-enrolled", headers=bearer(tokens["access_token"]))
    assert response.status_code == 204
    assert _device(db_session, tokens["device"]["id"]).biometric_enrolled_at is not None
    assert login(api).json()["device"]["biometric_enrolled"] is True


def test_push_token_set_and_clear(api: TestClient, db_session: Session) -> None:  # noqa: F811
    tokens = login(api).json()
    headers = bearer(tokens["access_token"])
    response = api.put(
        "/api/v1/me/device/push-token", json={"provider": "hms", "token": "abc"}, headers=headers
    )
    assert response.status_code == 204
    device = _device(db_session, tokens["device"]["id"])
    assert (device.push_provider, device.push_token) == ("hms", "abc")
    assert device.push_token_updated_at is not None

    api.put("/api/v1/me/device/push-token", json={"provider": "fcm", "token": ""}, headers=headers)
    device = _device(db_session, tokens["device"]["id"])
    assert (device.push_provider, device.push_token) == ("none", None)


def test_push_token_invalid_provider_400(api: TestClient) -> None:  # noqa: F811
    tokens = login(api).json()
    response = api.put(
        "/api/v1/me/device/push-token",
        json={"provider": "apns", "token": "x"},
        headers=bearer(tokens["access_token"]),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_revoked_device_gets_401(api: TestClient) -> None:  # noqa: F811
    tokens = login(api).json()
    headers = bearer(tokens["access_token"])
    api.post("/api/v1/auth/logout", headers=headers)
    for response in (
        api.put("/api/v1/me/device/push-token", json={"provider": "fcm", "token": "x"}, headers=headers),
        api.post("/api/v1/me/device/biometric-enrolled", headers=headers),
        api.get("/api/v1/me/config", headers=headers),
    ):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "token_invalid"
